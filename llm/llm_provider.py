from langchain_ollama import ChatOllama


DEFAULT_MODEL = "llama3.2:3b"


def get_llm(model_name: str | None = None):
    model_name = model_name or DEFAULT_MODEL

    print(f"[LLM PROVIDER] Using model: {model_name}")

    return ChatOllama(
        model=model_name,
        temperature=0
    )