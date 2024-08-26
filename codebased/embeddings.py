import typing as T

from openai import OpenAI

from codebased.core import EmbeddingsConfig
from codebased.models import Embedding, ObjectHandle
from codebased.parser import render_object


def create_openai_embeddings_sync_batched(
        client: OpenAI,
        objects: T.List[ObjectHandle],
        config: EmbeddingsConfig
) -> T.Iterable[Embedding]:
    text = [render_object(o) for o in objects]
    response = client.embeddings.create(input=text, model=config.model, dimensions=config.dimensions)
    return [
        Embedding(
            object_id=o.id,
            data=e.data,
            content_hash=o.object.hash
        )
        for o, e in zip(objects, response.data)
    ]
