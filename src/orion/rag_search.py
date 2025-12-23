import asyncio
import logging

from dotenv import load_dotenv

load_dotenv()
from orion.rag.vector_store import VectorStore

logging.basicConfig(level=logging.INFO)


async def search_demo():
    store = VectorStore()

    query = "Show me oversized RSI trades for QQQ"
    print(f"\nQuery: '{query}'\n")

    docs = await store.search(query, k=3)

    for d in docs:
        print(f"--- Doc: {d.doc_id} ---")
        print(f"Content: {d.content}")
        print(f"Meta: {d.metadata_json}")
        print("-" * 30)


if __name__ == "__main__":
    asyncio.run(search_demo())
