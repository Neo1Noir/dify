"""Exercise attachment expansion using real SQLite joins and mocked storage/RAG."""

import base64
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.workflow.nodes.knowledge_retrieval.retrieval import Source, SourceMetadata
from models.dataset import SegmentAttachmentBinding
from models.model import UploadFile
from services.entities.knowledge_retrieval_inner import InnerKnowledgeRetrieveRequest
from services.knowledge_retrieval_inner_service import InnerKnowledgeRetrievalService


class KnowledgeRetrievalImageTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        UploadFile.__table__.create(self.engine)
        SegmentAttachmentBinding.__table__.create(self.engine)
        self.tenant, self.dataset, self.document, self.segment = [str(uuid4()) for _ in range(4)]
        self.request = InnerKnowledgeRetrieveRequest.model_validate(
            {
                "caller": {
                    "tenant_id": self.tenant,
                    "user_id": str(uuid4()),
                    "app_id": str(uuid4()),
                    "user_from": "account",
                    "invoke_from": "debugger",
                },
                "dataset_ids": [self.dataset],
                "query": "Figure 3.1",
                "retrieval": {"mode": "multiple", "top_k": 4},
            }
        )
        self.source = Source(
            metadata=SourceMetadata(
                dataset_id=self.dataset,
                dataset_name="Manual",
                document_id=self.document,
                document_name="manual.pdf",
                data_source_type="upload_file",
                segment_id=self.segment,
            ),
            title="manual.pdf",
            content="Figure 3.1",
            files=None,
        )

    def tearDown(self):
        self.engine.dispose()

    def add_image(self, *, upload_tenant=None, mime_type="image/png", **binding_overrides):
        file_id = str(uuid4())
        with self.engine.begin() as connection:
            connection.execute(
                UploadFile.__table__.insert().values(
                    id=file_id,
                    tenant_id=upload_tenant or self.tenant,
                    storage_type="local",
                    key="images/test.png",
                    name="test.png",
                    size=11,
                    extension="png",
                    mime_type=mime_type,
                    created_by=self.request.caller.user_id,
                )
            )
            binding = {
                "tenant_id": self.tenant,
                "dataset_id": self.dataset,
                "document_id": self.document,
                "segment_id": self.segment,
                "attachment_id": file_id,
            }
            binding.update(binding_overrides)
            connection.execute(SegmentAttachmentBinding.__table__.insert().values(**binding))

    def retrieve(self):
        rag = MagicMock()
        rag.knowledge_retrieval.return_value = [self.source]
        rag.llm_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "prompt_unit_price": "0",
            "completion_unit_price": "0",
            "prompt_price_unit": "0.001",
            "completion_price_unit": "0.001",
            "prompt_price": "0",
            "completion_price": "0",
            "total_price": "0",
            "currency": "USD",
            "latency": 0,
        }
        with (
            Session(self.engine) as session,
            patch.object(InnerKnowledgeRetrievalService, "_validate_caller_app"),
            patch.object(InnerKnowledgeRetrievalService, "_validate_datasets"),
            patch("services.knowledge_retrieval_inner_service.DatasetRetrieval", return_value=rag),
        ):
            return InnerKnowledgeRetrievalService().retrieve(self.request, session)

    def test_text_hit_expands_bound_image_without_image_vector_hit(self):
        self.add_image()
        with patch("services.knowledge_retrieval_inner_service.storage.load_once", return_value=b"image-bytes") as load:
            response = self.retrieve()
        load.assert_called_once_with("images/test.png")
        image = response.results[0].files[0]
        assert base64.b64decode(image["base64_data"]) == b"image-bytes"
        assert image["mime_type"] == "image/png"
        assert image["extension"] == ".png"
        assert response.results[0].content == "Figure 3.1"

    def test_excludes_mismatched_owner_chain_and_nonimages(self):
        for field in ("tenant_id", "dataset_id", "document_id", "segment_id"):
            self.add_image(**{field: str(uuid4())})
        self.add_image(upload_tenant=str(uuid4()))
        self.add_image(mime_type="application/pdf")
        with patch("services.knowledge_retrieval_inner_service.storage.load_once") as load:
            response = self.retrieve()
        load.assert_not_called()
        assert response.results[0].files is None

    def test_text_without_images_keeps_original_response(self):
        with patch("services.knowledge_retrieval_inner_service.storage.load_once") as load:
            response = self.retrieve()
        load.assert_not_called()
        assert response.results[0].files is None


if __name__ == "__main__":
    unittest.main()
