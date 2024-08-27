import typing as T

from openai import OpenAI

from codebased.core import EmbeddingsConfig
from codebased.models import Embedding, EmbeddingRequest


def get_embedding_kwargs(config: EmbeddingsConfig) -> dict:
    kwargs = {"model": config.model}
    if config.model in {'text-embedding-3-large', 'text-embedding-3-small'}:
        kwargs["dimensions"] = config.dimensions
    return kwargs


def create_openai_embeddings_sync_batched(
        client: OpenAI,
        embedding_requests: T.List[EmbeddingRequest],
        config: EmbeddingsConfig
) -> T.Iterable[Embedding]:
    text = [o.content for o in embedding_requests]
    response = client.embeddings.create(input=text, **get_embedding_kwargs(config))
    return [
        Embedding(
            object_id=o.object_id,
            data=e.embedding,
            content_hash=o.content_hash
        )
        for o, e in zip(embedding_requests, response.data)
    ]


def create_ephemeral_embedding(client: OpenAI, text: str, config: EmbeddingsConfig) -> list[float]:
    response = client.embeddings.create(input=text, **get_embedding_kwargs(config))
    return response.data[0].embedding
