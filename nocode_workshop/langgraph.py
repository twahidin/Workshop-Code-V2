from langchain.chat_models import ChatOpenAI
import streamlit as st
import json
import functools
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain import hub
from langchain.agents import Tool, create_react_agent
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_community.utilities import GoogleSerperAPIWrapper
import operator
from typing import Annotated, List, Sequence, Tuple, TypedDict, Union
import os
from typing import TypedDict, Annotated, Union
from langchain_core.agents import AgentAction, AgentFinish
from langchain_core.messages import BaseMessage
import operator
from typing import TypedDict, Annotated
from langchain_core.tools import tool
from langchain_core.agents import AgentFinish
from langgraph.prebuilt.tool_executor import ToolExecutor
from langgraph.prebuilt import ToolInvocation
from langgraph.graph import END, StateGraph
from langchain_core.agents import AgentActionMessageLog
from langchain_experimental.utilities import PythonREPL
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.tools.render import format_tool_to_openai_function
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ChatMessage,
    FunctionMessage,
    HumanMessage,
)

# This defines the object that is passed between each node
# in the graph. We will create different nodes for each agent and tool
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    sender: str

tavily_tool = TavilySearchResults(max_results=5)
repl = PythonREPL()

@tool
def python_repl(
    code: Annotated[str, "The python code to execute to generate your chart."]):
    """Use this to execute python code. If you want to see the output of a value,
    you should print it out with `print(...)`. This is visible to the user."""
    try:
        result = repl.run(code)
    except BaseException as e:
        return f"Failed to execute. Error: {repr(e)}"
    return f"Succesfully executed:\n```python\n{code}\n```\nStdout: {result}"


tools = [tavily_tool, python_repl]
tool_executor = ToolExecutor(tools)

# llm = ChatOpenAI(model="gpt-4-1106-preview")


def create_agent(llm, tools, system_message: str):
    """Create an agent."""
    functions = [format_tool_to_openai_function(t) for t in tools]

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are a helpful AI assistant, collaborating with other assistants."
                " Use the provided tools to progress towards answering the question."
                " If you are unable to fully answer, that's OK, another assistant with different tools "
                " will help where you left off. Execute what you can to make progress."
                " If you or any of the other assistants have the final answer or deliverable,"
                " prefix your response with FINAL ANSWER so the team knows to stop."
                " You have access to the following tools: {tool_names}.\n{system_message}",
            ),
            MessagesPlaceholder(variable_name="messages"),
        ]
    )
    prompt = prompt.partial(system_message=system_message)
    prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
    return prompt | llm.bind_functions(functions)


# Helper function to create a node for a given agent
def agent_node(state, agent, name):
    result = agent.invoke(state)
    # We convert the agent output into a format that is suitable to append to the global state
    if isinstance(result, FunctionMessage):
        pass
    else:
        result = HumanMessage(**result.dict(exclude={"type", "name"}), name=name)
    return {
        "messages": [result],
        # Since we have a strict workflow, we can
        # track the sender so we know who to pass to next.
        "sender": name,
    }

def tool_node(state):
        """This runs tools in the graph

        It takes in an agent action and calls that tool and returns the result."""
        messages = state["messages"]
        # Based on the continue condition
        # we know the last message involves a function call
        last_message = messages[-1]
        # We construct an ToolInvocation from the function_call
        tool_input = json.loads(
            last_message.additional_kwargs["function_call"]["arguments"]
        )
        # We can pass single-arg inputs by value
        if len(tool_input) == 1 and "__arg1" in tool_input:
            tool_input = next(iter(tool_input.values()))
        tool_name = last_message.additional_kwargs["function_call"]["name"]
        action = ToolInvocation(
            tool=tool_name,
            tool_input=tool_input,
        )
        # We call the tool_executor and get back a response
        response = tool_executor.invoke(action)
        # We use the response to create a FunctionMessage
        function_message = FunctionMessage(
            content=f"{tool_name} response: {str(response)}", name=action.tool
        )
        # We return a list, because this will get added to the existing list
        return {"messages": [function_message]}



def langgraph_function():
    tavily_tool = TavilySearchResults(max_results=5)
    
    

    llm = ChatOpenAI(model="gpt-4-1106-preview")

    # Psychologist agent and node
    psychologist_agent = create_agent(
        llm,
        [tavily_tool],
        system_message="An expert psychologist who seamlessly weaves neuroscientific theories into conversations, unraveling the complexities of human behavior and emotions.",
    )
    psychologist_node = functools.partial(agent_node, agent=psychologist_agent, name="Psychologist")

    # Socioligist
    sociologist_agent = create_agent(
        llm,
        [tavily_tool],
        system_message="A keen-eyed sociologist adept at dissecting societal patterns, investigating the collective psyche. Focus on group effects rather than individual effects.",
    )
    sociologist_node = functools.partial(agent_node, agent=sociologist_agent, name="Sociologist")

    # Socioligist
    economist_agent = create_agent(
        llm,
        [tavily_tool],
        system_message="A pragmatic economist who quantifies intangibles, connecting trends to economic implications with precision.",
    )
    economist_node = functools.partial(agent_node, agent=economist_agent, name="Economist")


    # Research agent and node
    research_agent = create_agent(
        llm,
        [tavily_tool],
        system_message="You should provide accurate data for the chart generator to use.",
    )
    research_node = functools.partial(agent_node, agent=research_agent, name="Researcher")

    # Chart Generator
    chart_agent = create_agent(
        llm,
        [python_repl],
        system_message="Any charts you display will be visible by the user.",
    )
    chart_node = functools.partial(agent_node, agent=chart_agent, name="Chart Generator")

    

    # Either agent can decide to end
    def router(state):
        # This is the router
        messages = state["messages"]
        last_message = messages[-1]
        if "function_call" in last_message.additional_kwargs:
            # The previus agent is invoking a tool
            return "call_tool"
        if "FINAL ANSWER" in last_message.content:
            # Any agent decided the work is done
            return "end"
        return "continue"

    #researcher and chart generator agent workflow 

    def research_chart_agent():

        workflow = StateGraph(AgentState)

        workflow.add_node("Researcher", research_node)
        workflow.add_node("Chart Generator", chart_node)
        workflow.add_node("call_tool", tool_node)

        workflow.add_conditional_edges(
            "Researcher",
            router,
            {"continue": "Chart Generator", "call_tool": "call_tool", "end": END},
        )
        workflow.add_conditional_edges(
            "Chart Generator",
            router,
            {"continue": "Researcher", "call_tool": "call_tool", "end": END},
        )

        workflow.add_conditional_edges(
            "call_tool",
            # Each agent node updates the 'sender' field
            # the tool calling node does not, meaning
            # this edge will route back to the original agent
            # who invoked the tool
            lambda x: x["sender"],
            {
                "Researcher": "Researcher",
                "Chart Generator": "Chart Generator",
            },
        )
        workflow.set_entry_point("Researcher")
        graph = workflow.compile()

        return graph

    graph = research_chart_agent()
    state = {"messages": []}
    while True:
        state = graph(state)
        if state is AgentFinish:
            break
    return state