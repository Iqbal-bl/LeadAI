"""
Simplest possible LangChain + OpenAI test — just asks one question and
prints the answer. No CRAG, no agent, no tools. Use this to confirm the
key works at all, in complete isolation from the rest of the project.

    python simple_openai_test.py
"""

import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

llm = ChatOpenAI(model="gpt-4o-mini", api_key=os.environ["OPENAI_API_KEY"])

question = "What is the capital of France?"
response = llm.invoke(question)

print("Question:", question)
print("Answer:", response.content)