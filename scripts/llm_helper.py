"""
Thin wrapper around llama.cpp's llama-cli for one-shot Q&A.

Why subprocess instead of a Python binding: llama-cpp-python builds were
more fragile on this ARM/Armbian combo than the plain llama.cpp CLI binary.
The --single-turn --simple-io -no-cnv flags make llama-cli print just the
response and exit, instead of staying in an interactive REPL.

See docs/JOURNEY.md for the model comparison (speed vs. quality) that led
to picking Gemma-2-2B over smaller/faster Qwen2.5 variants.
"""

import subprocess
import re

LLAMA_CLI = "/root/llama.cpp/build/bin/llama-cli"
LLM_MODEL_PATH = "/root/llm-models/gemma-2-2b-it-Q4_K_M.gguf"

SYSTEM_PROMPT = (
    "Ти — голосовий помічник у розумному домі. "
    "Відповідай коротко, українською мовою, максимум 2-3 речення. "
    "Якщо не знаєш точної відповіді — чесно скажи про це."
)


def ask_llm(question: str, max_tokens: int = 80, threads: int = 4) -> str:
    """Ask the local LLM a question and return a clean, single-paragraph answer.

    Expect this to take roughly 30-60 seconds on a 4-core ARM CPU without a
    GPU — call this from a place in your pipeline that can afford to wait,
    and consider giving the user an audible "thinking" cue first.
    """
    full_prompt = f"{SYSTEM_PROMPT}\n\nПитання: {question}"

    result = subprocess.run(
        [
            LLAMA_CLI,
            "-m", LLM_MODEL_PATH,
            "-p", full_prompt,
            "-n", str(max_tokens),
            "-t", str(threads),
            "--single-turn", "--simple-io", "-no-cnv",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )

    output = result.stdout

    # The model's answer appears right after our prompt is echoed back,
    # and ends right before the "[ Prompt: ..." stats line.
    prompt_marker = full_prompt.strip().splitlines()[-1]
    idx = output.rfind(prompt_marker)
    after_prompt = output[idx + len(prompt_marker):] if idx != -1 else output
    after_prompt = after_prompt.split("[ Prompt:")[0]

    answer = after_prompt.strip()
    answer = re.sub(r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF]", "", answer)  # strip emoji
    answer = re.sub(r"\n+", " ", answer).strip()

    # If the token limit cut the answer mid-sentence, trim back to the last
    # full sentence rather than reading a half-finished word aloud.
    last_sentence_end = max(answer.rfind("."), answer.rfind("!"), answer.rfind("?"))
    if last_sentence_end != -1 and last_sentence_end > len(answer) * 0.4:
        answer = answer[:last_sentence_end + 1]

    return answer if answer else "Вибачте, не вдалось згенерувати відповідь."


if __name__ == "__main__":
    print(ask_llm("Що таке фотосинтез?"))
