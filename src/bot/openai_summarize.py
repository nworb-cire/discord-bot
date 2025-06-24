import openai
from bot.config import get_settings

settings = get_settings()
openai.api_key = settings.openai_key


async def summarize(text: str) -> tuple[str, str]:
    prompt = (
        "Provide two JSON fields: short (2 sentences) and long (5 sentences) summaries "
        f"of the book titled '{text}'."
    )
    response = await openai.ChatCompletion.acreate(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": prompt}],
    )
    data = response.choices[0].message.json
    return data["short"], data["long"]
