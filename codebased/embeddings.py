import typing as T

from openai import OpenAI

from codebased.core import EmbeddingsConfig
from codebased.models import Embedding, EmbeddingRequest


def create_openai_embeddings_sync_batched(
        client: OpenAI,
        embedding_requests: T.List[EmbeddingRequest],
        config: EmbeddingsConfig
) -> T.Iterable[Embedding]:
    text = [o.content for o in embedding_requests]
    kwargs = dict(input=text, model=config.model, dimensions=config.dimensions)
    if config.model not in {'text-embedding-3-large', 'text-embedding-3-small'}:
        kwargs.pop('dimensions')
    response = client.embeddings.create(**kwargs)
    return [
        Embedding(
            object_id=o.object_id,
            data=e.embedding,
            content_hash=o.content_hash
        )
        for o, e in zip(embedding_requests, response.data)
    ]
