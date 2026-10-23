"""Knowledge images must reach the model as pixels, including resumed eager state."""

import asyncio
import base64
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent, ModelMessagesTypeAdapter, UserPromptPart
from pydantic_ai.models.test import TestModel

from agenton.compositor import Compositor, LayerNode, LayerProvider
from dify_agent.layers.execution_context.layer import DifyExecutionContextLayer, DifyExecutionContextLayerConfig
from dify_agent.layers.knowledge.client import DifyKnowledgeBaseClient, DifyKnowledgeRetrieveResponse
from dify_agent.layers.knowledge.configs import DifyKnowledgeBaseLayerConfig, DifyKnowledgeEagerResult
from dify_agent.layers.knowledge.layer import DifyKnowledgeBaseLayer, _image_contents


class KnowledgeImageTests(unittest.TestCase):
    def setUp(self):
        self.image = {
            "id": "image-1",
            "source": "Figure 3.1 structure drawing",
            "mime_type": "image/png",
            "base64_data": base64.b64encode(b"image-bytes").decode(),
        }
        self.response = DifyKnowledgeRetrieveResponse.model_validate(
            {
                "results": [
                    {
                        "metadata": {"dataset_id": "dataset-1", "document_name": "manual.pdf"},
                        "title": "diagram",
                        "content": "Figure 3.1",
                        "files": [self.image],
                    }
                ]
            }
        )
        self.config = DifyKnowledgeBaseLayerConfig.model_validate(
            {
                "sets": [
                    {
                        "id": "set-1",
                        "name": "Manual",
                        "datasets": [{"id": "dataset-1"}],
                        "query": {"mode": "generated_query"},
                        "retrieval": {"mode": "multiple", "top_k": 4},
                    }
                ]
            }
        )

    def compositor(self):
        return Compositor(
            [
                LayerNode(
                    "execution_context",
                    LayerProvider.from_factory(
                        layer_type=DifyExecutionContextLayer,
                        create=lambda config: DifyExecutionContextLayer.from_config_with_settings(
                            DifyExecutionContextLayerConfig.model_validate(config),
                            daemon_url="http://daemon",
                            daemon_api_key="test",
                        ),
                    ),
                ),
                LayerNode(
                    "knowledge",
                    LayerProvider.from_factory(
                        layer_type=DifyKnowledgeBaseLayer,
                        create=lambda config: DifyKnowledgeBaseLayer.from_config_with_settings(
                            config, inner_api_url="http://api", inner_api_key="test"
                        ),
                    ),
                    deps={"execution_context": "execution_context"},
                ),
            ]
        )

    def test_generated_query_sends_native_image_through_real_agent(self):
        async def scenario():
            def respond(request):
                return httpx.Response(200, json=self.response.model_dump(mode="json"))

            async with (
                httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client,
                self.compositor().enter(
                    configs={
                        "execution_context": {
                            "tenant_id": "tenant-1",
                            "user_id": "user-1",
                            "app_id": "app-1",
                            "user_from": "account",
                            "agent_mode": "agent_app",
                            "invoke_from": "debugger",
                        },
                        "knowledge": self.config,
                    }
                ) as run,
            ):
                layer = run.get_layer("knowledge", DifyKnowledgeBaseLayer)
                tool = (await layer.get_tools(http_client=client))[0]
                agent = Agent(TestModel(custom_output_text="done"), tools=[tool])
                result = await agent.run("Find Figure 3.1")
                messages = ModelMessagesTypeAdapter.validate_json(
                    ModelMessagesTypeAdapter.dump_json(result.all_messages())
                )
                images = [
                    content
                    for message in messages
                    for part in message.parts
                    if isinstance(part, UserPromptPart) and isinstance(part.content, list)
                    for content in part.content
                    if isinstance(content, BinaryContent)
                ]
                self.assertEqual([image.data for image in images], [b"image-bytes"])

        asyncio.run(scenario())

    def test_text_only_results_keep_string_return(self):
        async def scenario():
            layer = DifyKnowledgeBaseLayer.from_config_with_settings(
                self.config, inner_api_url="http://api", inner_api_key="test"
            )
            response = self.response.model_copy(deep=True)
            response.results[0].files = []
            async with httpx.AsyncClient() as http_client:
                client = DifyKnowledgeBaseClient(base_url="http://api", api_key="test", http_client=http_client)
                with patch.object(DifyKnowledgeBaseClient, "retrieve", new=AsyncMock(return_value=response)):
                    result = await layer._retrieve_for_set(
                        client=client,
                        caller={
                            "tenant_id": "t",
                            "user_id": "u",
                            "app_id": "a",
                            "user_from": "account",
                            "invoke_from": "debugger",
                        },
                        knowledge_set=self.config.sets[0],
                        query="figure",
                        retryable_observation=True,
                    )
            self.assertIsInstance(result, str)
            self.assertIn("Figure 3.1", result)

        asyncio.run(scenario())

    def test_eager_images_survive_json_state(self):
        async def scenario():
            config = self.config.model_copy(deep=True)
            config.sets[0].query.mode = "user_query"
            config.sets[0].query.value = "figure"
            with patch.object(DifyKnowledgeBaseClient, "retrieve", new=AsyncMock(return_value=self.response)):
                async with self.compositor().enter(
                    configs={
                        "execution_context": {
                            "tenant_id": "tenant-1",
                            "user_id": "user-1",
                            "app_id": "app-1",
                            "user_from": "account",
                            "agent_mode": "agent_app",
                            "invoke_from": "debugger",
                        },
                        "knowledge": config,
                    }
                ) as run:
                    layer = run.get_layer("knowledge", DifyKnowledgeBaseLayer)
                    restored = type(layer.runtime_state).model_validate_json(layer.runtime_state.model_dump_json())
                    self.assertEqual(_image_contents(restored.eager_results[0].files)[1].data, b"image-bytes")
                    self.assertIsInstance(layer.user_prompts[2], BinaryContent)
            old = DifyKnowledgeEagerResult(set_id="s", set_name="n", query="q", observation="text", status="success")
            self.assertEqual(old.files, [])

        asyncio.run(scenario())

    def test_images_deduplicate_and_skip_nonimage_references(self):
        self.assertEqual(
            len(
                _image_contents(
                    [
                        self.image,
                        self.image,
                        {"id": "other", "mime_type": "application/pdf", "base64_data": self.image["base64_data"]},
                        {"id": "url-only", "source_url": "http://private"},
                    ]
                )
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()
