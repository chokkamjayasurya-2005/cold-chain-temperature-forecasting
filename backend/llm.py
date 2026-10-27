from backend.schemas import ChatRequest
from services.llm_service import generate_ai_explanation


def explain_prediction(request: ChatRequest) -> str:
    conversation = "\n".join(
        f"{message.role.title()}: {message.content}"
        for message in request.conversation
    )
    question = request.question
    if conversation:
        question = f"Previous conversation:\n{conversation}\n\nFollow-up question: {question}"
    return generate_ai_explanation(
        question=question,
        context=request.container_context.model_dump(mode="json"),
    )
