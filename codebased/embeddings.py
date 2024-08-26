import typing as T

from openai import OpenAI

from codebased.models import Embedding, Object


def create_openai_embeddings_sync_batched(client: OpenAI, objects: T.List[Object]) -> T.Iterable[Embedding]:
    pass
