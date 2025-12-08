import os
import sys
import json
import subprocess
import threading
import queue
import logging
from typing import List, Dict, Any, Optional
from openai import OpenAI
from logger import setup_logging # 仍然需要导入 setup_logging 以确保 Root Logger 被配置

# 获取 Agent 模块的 Logger 实例
logger = logging.getLogger(__name__)

def get_env_variable(var_name: str) -> str:
    val = os.getenv(var_name)
    if not val:
        logger.warning(f"环境变量 {var_name} 未设置")
        return ""
    return val


class MCPClient:
    """
    一个最小化的 MCP (Model Context Protocol) 客户端，
    用于通过 Stdio 与 MCP Server (如 Chrome DevTools) 进行通信。
    """

    def __init__(self, command: str, args: List[str]):
        logger.info(f"启动 MCP Client: {command} {' '.join(args)}")
        try:
            self.process = subprocess.Popen(
                [command] + args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=sys.stderr,
                text=True,
                bufsize=1,  # Line buffered
            )
        except Exception as e:
            logger.error(f"启动 MCP 子进程失败: {e}")
            raise

        self.request_id = 0
        self.response_queue = queue.Queue()
        self.tools = []

        # 启动后台线程读取 stdout
        self.reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self.reader_thread.start()

        self._initialize()

    def _read_loop(self):
        while True:
            if self.process.poll() is not None:
                break
            try:
                line = self.process.stdout.readline()
                if not line:
                    break
                # logger.debug(f"[MCP RX] {line.strip()}")
                try:
                    msg = json.loads(line)
                    self.response_queue.put(msg)
                except json.JSONDecodeError:
                    logger.warning(f"[MCP Error] 无法解析 JSON: {line}")
            except Exception as e:
                logger.error(f"[MCP Read Error] {e}")
                break

    def _send(self, message: Dict[str, Any]):
        json_str = json.dumps(message)
        # logger.debug(f"[MCP TX] {json_str}")
        try:
            self.process.stdin.write(json_str + "\n")
            self.process.stdin.flush()
        except BrokenPipeError:
            logger.error("MCP 管道已断开")

    def _wait_for_response(self, req_id: int, timeout: int = 30) -> Dict[str, Any]:
        """简单的同步等待响应"""
        # 在实际复杂场景中可能需要更完善的 Future/Promise 机制
        # 这里简化为循环检查 Queue
        start_time = (
            threading.Event()
        )  # This line seems to be a placeholder and not used for actual timeout logic.
        while not start_time.wait(
            timeout=0.1
        ):  # Simple polling, not a real timeout mechanism.
            # 这里其实不是真正的 timeout 逻辑，为了简化代码，暂不引入复杂的时间计算
            # 实际项目中应记录 start time 并检查 elapsed
            try:
                # 使用 queue 的 get(timeout=...) 更好
                msg = self.response_queue.get(block=True, timeout=30)
                if msg.get("id") == req_id:
                    return msg
                # 忽略非匹配的消息 (如 notifications)
            except queue.Empty:
                return {"error": "Timeout waiting for response"}
        return {"error": "Unknown error"}

    def _initialize(self):
        req_id = self.request_id
        self.request_id += 1

        init_msg = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "python-agent-client", "version": "1.0"},
            },
        }
        self._send(init_msg)
        # 第一次握手可能较慢
        response = self.response_queue.get(block=True, timeout=10)

        if response.get("id") != req_id:
            # 简化的处理，实际应该 loop wait
            pass

        if "error" in response:
            raise Exception(f"MCP Initialize failed: {response['error']}")

        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        logger.info("MCP Session Initialized.")

    def list_tools(self) -> List[Dict[str, Any]]:
        req_id = self.request_id
        self.request_id += 1

        self._send({"jsonrpc": "2.0", "id": req_id, "method": "tools/list"})

        # 简单循环等待直到匹配 ID
        while True:
            try:
                response = self.response_queue.get(timeout=10)
                if response.get("id") == req_id:
                    break
            except queue.Empty:
                logger.error("Timeout listing tools")
                return []

        if "error" in response:
            logger.error(f"Failed to list tools: {response['error']}")
            return []

        self.tools = response["result"].get("tools", [])
        return self.tools

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        req_id = self.request_id
        self.request_id += 1

        logger.info(
            f"调用 MCP 工具: {name} | 参数: {json.dumps(arguments, ensure_ascii=False)}"
        )

        self._send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )

        while True:
            try:
                response = self.response_queue.get(
                    timeout=60
                )  # 工具执行可能较慢，给 60s
                if response.get("id") == req_id:
                    break
            except queue.Empty:
                return "Error: Tool execution timed out."

        if "error" in response:
            return f"Error: {response['error']['message']}"

        content = response["result"].get("content", [])
        text_content = "\n".join([c["text"] for c in content if c["type"] == "text"])
        logger.debug(f"工具返回 (前100字符): {text_content[:100]}...")
        return text_content

    def get_openai_tools_schema(self) -> List[Dict[str, Any]]:
        openai_tools = []
        for tool in self.tools:
            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get(
                            "inputSchema", {"type": "object", "properties": {}}
                        ),
                    },
                }
            )
        return openai_tools

    def close(self):
        if self.process:
            self.process.terminate()


class Agent:
    def __init__(self, model_name: str, mcp_config: Optional[Dict[str, Any]] = None):
        self.model_name = model_name
        self.mcp_client: Optional[MCPClient] = None
        self.mcp_enabled = False

        api_key = get_env_variable("OPENAPI_API_KEY")
        api_ep = get_env_variable("OPENAPI_ENDPOINT")

        if not api_key or not api_ep:
            logger.error("缺少必要的环境变量 OPENAPI_API_KEY 或 OPENAPI_ENDPOINT")
            sys.exit(1)

        self.client = OpenAI(base_url=api_ep, api_key=api_key)

        if mcp_config:
            # 解析配置
            cmd = None
            args = []

            # 策略: 如果传入的 config 包含 "command"，则直接使用
            # 如果包含 "chrome-devtools" 等键，则取其值
            target_config = mcp_config
            if "command" not in mcp_config:
                # 尝试寻找第一个包含 command 的 value
                for key, val in mcp_config.items():
                    if isinstance(val, dict) and "command" in val:
                        target_config = val
                        break

            if "command" in target_config:
                cmd = target_config["command"]
                args = target_config.get("args", [])

            if cmd:
                try:
                    self.mcp_client = MCPClient(cmd, args)
                    # 预加载工具列表
                    self.mcp_client.list_tools()
                    self.mcp_enabled = True
                except Exception as e:
                    logger.error(f"MCP Client 初始化失败: {e}")
            else:
                logger.error(
                    "提供了 mcp_config 但无法解析出有效的 'command'。MCP 未启用。"
                )

    def chat(self, messages: List[Dict[str, str]]) -> str:
        """
        普通对话，不涉及工具调用。
        """
        logger.info(f"[Chat] User: {messages[-1].get('content')[:100]}...")

        try:
            response = self.client.chat.completions.create(
                model=self.model_name, messages=messages
            )
            content = response.choices[0].message.content
            logger.info(f"[Chat] Model: {content[:100]}...")
            return content
        except Exception as e:
            logger.error(f"Chat completion failed: {e}")
            raise

    def chat_with_tools(self, messages: List[Dict[str, Any]]) -> str:
        """
        支持 MCP 工具调用的对话循环。
        """
        if not self.mcp_enabled or not self.mcp_client:
            logger.warning("MCP 未启用，回退到普通 Chat 模式")
            return self.chat(messages)

        logger.info(f"[ToolChat] Start. User: {messages[-1].get('content')[:100]}...")

        tools_schema = self.mcp_client.get_openai_tools_schema()

        # 循环限制，防止死循环
        max_turns = 10
        turn = 0

        while turn < max_turns:
            turn += 1
            try:
                completion = self.client.chat.completions.create(
                    model=self.model_name, messages=messages, tools=tools_schema
                )

                msg = completion.choices[0].message

                # Case 1: 模型决定调用工具
                if completion.choices[0].finish_reason == "tool_calls":
                    logger.info(
                        f"[ToolChat] Turn {turn}: Model 请求调用 {len(msg.tool_calls)} 个工具"
                    )

                    # 必须把这一轮的 assistant message (包含 tool_calls) 加入历史
                    messages.append(msg.model_dump())

                    for tool_call in msg.tool_calls:
                        name = tool_call.function.name
                        args_json = tool_call.function.arguments
                        try:
                            args = json.loads(args_json)
                        except json.JSONDecodeError:
                            logger.error(f"工具参数 JSON 解析失败: {args_json}")
                            args = {}

                        # 执行 MCP
                        result = self.mcp_client.call_tool(name, args)

                        # 回填结果
                        messages.append(
                            {
                                "role": "tool",
                                "content": result,
                                "tool_call_id": tool_call.id,
                            }
                        )

                # Case 2: 模型完成回答
                else:
                    final_content = msg.content
                    logger.info(
                        f"[ToolChat] Completed. Model: {final_content[:100]}..."
                    )
                    return final_content

            except Exception as e:
                logger.error(f"ToolChat error on turn {turn}: {e}")
                raise

        return "Error: Maximum conversation turns exceeded."

    def close(self):
        if self.mcp_client:
            self.mcp_client.close()
