import os
import sys
import json
import logging
import asyncio
from contextlib import AsyncExitStack
from typing import List, Dict, Any, Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, BaseMessage, SystemMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langgraph.prebuilt import create_react_agent

# MCP Integration
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_mcp_adapters.tools import load_mcp_tools

from logger import setup_logging

# 获取 Agent 模块的 Logger 实例
logger = logging.getLogger(__name__)

def get_env_variable(var_name: str) -> str:
    val = os.getenv(var_name)
    if not val:
        logger.warning(f"环境变量 {var_name} 未设置")
        return ""
    return val

class Agent:
    """
    LangChain Agent with native MCP Support (Async)
    """
    def __init__(self, model_name: str, mcp_config: Optional[Dict[str, Any]] = None, allowed_tools: Optional[List[str]] = None, max_tokens: Optional[int] = None):
        self.model_name = model_name
        self.mcp_config = mcp_config
        self.allowed_tools = allowed_tools
        self.graph = None
        self.exit_stack = AsyncExitStack()
        self.session: Optional[ClientSession] = None
        
        # LLM Initialization
        api_key = get_env_variable("OPENAPI_API_KEY")
        api_ep = get_env_variable("OPENAPI_ENDPOINT")
        
        if not api_key or not api_ep:
            logger.error("缺少必要的环境变量 OPENAPI_API_KEY 或 OPENAPI_ENDPOINT")
            sys.exit(1)

        self.llm = ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url=api_ep,
            temperature=0.7,
            streaming=True,
            max_tokens=max_tokens
        )

    async def __aenter__(self):
        """
        Async Context Entry: Initialize MCP Connection and Tools
        """
        tools = []
        if self.mcp_config:
            # Extract Command and Args
            target_config = self.mcp_config
            if "command" not in self.mcp_config:
                # Fallback to finding nested config
                for val in self.mcp_config.values():
                    if isinstance(val, dict) and "command" in val:
                        target_config = val
                        break
            
            cmd = target_config.get("command")
            args = target_config.get("args", [])

            if cmd:
                logger.info(f"Connecting to MCP Server: {cmd} {args}")
                server_params = StdioServerParameters(command=cmd, args=args)
                
                try:
                    # Initialize MCP Client
                    # stdio_client returns (read_stream, write_stream)
                    # We use AsyncExitStack to manage the context managers
                    read, write = await self.exit_stack.enter_async_context(stdio_client(server_params))
                    self.session = await self.exit_stack.enter_async_context(ClientSession(read, write))
                    
                    await self.session.initialize()
                    logger.info("MCP Session Initialized")
                    
                    # Load Tools using Adapter
                    all_tools = await load_mcp_tools(self.session)
                    logger.info(f"Loaded {len(all_tools)} tools from MCP Server. Names: {[t.name for t in all_tools]}")
                    
                    if self.allowed_tools:
                        tools = [t for t in all_tools if t.name in self.allowed_tools]
                        logger.info(f"Filtered tools to ({len(tools)}): {[t.name for t in tools]}")
                    else:
                        tools = all_tools
                    
                except Exception as e:
                    logger.error(f"Failed to initialize MCP connection: {e}")
                    # Decide whether to fail hard or continue without tools
                    # raise e 

        # Create Agent Graph
        self.graph = create_react_agent(self.llm, tools)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        Cleanup MCP connection
        """
        await self.exit_stack.aclose()
        logger.info("MCP Session Closed")



    async def achat(self, messages: List[Dict[str, str]]) -> str:
        """
        Async Chat (No Tools) - Handles full conversation history
        """
        lc_messages = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "system":
                lc_messages.append(SystemMessage(content=content))
            elif role == "user":
                lc_messages.append(HumanMessage(content=content))
            elif role == "assistant":
                lc_messages.append(AIMessage(content=content))
            else:
                lc_messages.append(HumanMessage(content=content)) # Default back to user if unknown

        try:
            response = await self.llm.ainvoke(lc_messages)
            return str(response.content)
        except Exception as e:
            logger.error(f"Chat failed: {e}")
            return ""

    def chat(self, messages: List[Dict[str, str]]) -> str:
        """
        Sync Chat (No Tools) wrapper
        """
        return asyncio.run(self.achat(messages))

    async def achat_with_tools(self, messages: List[Dict[str, str]]) -> str:
        """
        Async Chat with Tools (via LangGraph)
        """
        logger.info(f"[Agent] Processing request: {messages[-1].get('content', '')[:50]}...")
        
        try:
            # invoke graph
            result = await self.graph.ainvoke({"messages": messages})
            final_msg = result["messages"][-1]
            return str(final_msg.content)
        except Exception as e:
            logger.error(f"Agent execution failed: {e}")
            return f"Error: {e}"

    async def achat_with_tools_full(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Async Chat with Tools returning FULL STATE (for manual history inspection)
        """
        logger.info(f"[Agent] Processing request (Full State): {messages[-1].get('content', '')[:50]}...")
        
        if not self.graph:
            raise RuntimeError("Agent graph not initialized")

        # invoke graph
        return await self.graph.ainvoke({"messages": messages})

    # --- Sync Compatibility Layer (Deprecated) ---
    # Since we are moving to async, these are just helpers if really needed, 
    # but the calling code should ideally be updated.
    
    def chat_with_tools(self, messages: List[Dict[str, str]]) -> str:
       # Verify if we are in a loop
       try:
           loop = asyncio.get_running_loop()
       except RuntimeError:
           loop = None
       
       if loop and loop.is_running():
            raise RuntimeError("Cannot call sync `chat_with_tools` from inside an async loop. Use `achat_with_tools`.")
       
       return asyncio.run(self._run_sync_task(messages))

    async def _run_sync_task(self, messages):
        async with self:
            return await self.achat_with_tools(messages)

    def close(self):
        # No-op in sync mode if using context manager, but required for backward compatibility check
        pass
