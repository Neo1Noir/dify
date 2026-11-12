"""Explicit native plugin images preserve bytes and leave old tools unchanged."""
import asyncio
import base64
import unittest
from unittest.mock import AsyncMock
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent, ModelMessagesTypeAdapter, ToolReturn, UserPromptPart
from pydantic_ai.models.test import TestModel
from dify_agent.layers.dify_plugin.configs import DifyPluginToolConfig
from dify_agent.layers.dify_plugin.tool_client import DifyPluginToolInvokeMessage as Message
from dify_agent.layers.dify_plugin.tools_layer import _build_pydantic_ai_tool, _convert_tool_response_to_text


class PluginImageTests(unittest.TestCase):
    def blob(self, flag=True, mime="image/png", raw=b"original-bytes"):
        return Message.model_validate({"type": "blob", "message": {"blob": base64.b64encode(raw).decode()},
            "meta": {"mime_type": mime, **({"agent_image": flag} if flag is not None else {})}})

    def tool(self, messages):
        client = AsyncMock()
        client.invoke.return_value = messages
        config = DifyPluginToolConfig(plugin_id="example/plugin", provider="provider", tool_name="search",
            credential_type="api-key", parameters_json_schema={"type": "object", "properties": {}, "required": []})
        return _build_pydantic_ai_tool(client=client, tool_config=config, effective_parameters=[], file_context=None)

    def test_real_agent_native_image_and_json_history(self):
        async def scenario():
            message = self.blob()
            self.assertIs(message.meta["agent_image"], True)
            tool = self.tool([Message(type="text", message=Message.TextMessage(text="original observation")), message])
            result = await Agent(TestModel(custom_output_text="done"), tools=[tool]).run("Original question")
            history = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(result.all_messages()))
            images = [item for value in history for part in value.parts
                if isinstance(part, UserPromptPart) and isinstance(part.content, list)
                for item in part.content if isinstance(item, BinaryContent)]
            self.assertEqual([item.data for item in images], [b"original-bytes"])
        asyncio.run(scenario())

    def test_strict_boolean_optin_and_original_observation(self):
        async def scenario():
            messages = [Message(type="text", message=Message.TextMessage(text="exact old text")), self.blob()]
            value = await self.tool(messages).function_schema.call({}, None)
            self.assertIsInstance(value, ToolReturn)
            self.assertEqual(value.return_value, "exact old text")
            self.assertEqual(value.content[0].data, b"original-bytes")
        asyncio.run(scenario())

    def test_absent_false_string_integer_preserve_old_behavior(self):
        async def scenario():
            for flag in [None, False, "true", 1]:
                messages = [self.blob(flag)]
                value = await self.tool(messages).function_schema.call({}, None)
                self.assertEqual(value, _convert_tool_response_to_text(messages))
                self.assertIsInstance(value, str)
        asyncio.run(scenario())

    def test_nonimage_empty_and_links_use_old_converter(self):
        async def scenario():
            for messages in [[self.blob(mime="application/pdf")], [self.blob(raw=b"")],
                    [Message(type="image_link", message=Message.TextMessage(text="http://external.invalid/image"))]]:
                value = await self.tool(messages).function_schema.call({}, None)
                self.assertEqual(value, _convert_tool_response_to_text(messages))
        asyncio.run(scenario())

    def test_json_and_duplicate_content_keep_original_text_contract(self):
        async def scenario():
            messages = [Message(type="json", message=Message.JsonMessage(json_object={"parent": "exact-parent"})),
                self.blob(raw=b"first"), self.blob(raw=b"second")]
            value = await self.tool(messages).function_schema.call({}, None)
            self.assertEqual(value.return_value, _convert_tool_response_to_text(messages[:1]))
            self.assertEqual([item.data for item in value.content], [b"first", b"second"])
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
