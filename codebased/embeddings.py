import typing as T

from openai import OpenAI

from codebased.models import Embedding, Object
from codebased.parser import render_object


def create_openai_embeddings_sync_batched(client: OpenAI, objects: T.List[Object]) -> T.Iterable[Embedding]:
    text = [render_object(o) for o in objects]
