import streamlit as st
import fitz
import os
import getpass
import pandas as pd
import re
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain.embeddings import HuggingFaceEmbeddings
from langchain.chat_models import init_chat_model
import json
import sqlite3

# Ensure API Key
if not os.environ.get("GROQ_API_KEY"):
    os.environ["GROQ_API_KEY"] = getpass.getpass("Enter API key for Groq: ")

llm = init_chat_model("deepseek-r1-distill-llama-70b", model_provider="groq")

# Database setup
def init_db():
    conn = sqlite3.connect("chapters.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS chapters 
                 (chapter_number TEXT, title TEXT, start_page INTEGER, end_page INTEGER)''')
    conn.commit()
    conn.close()

def save_chapters_to_db(chapters):
    conn = sqlite3.connect("chapters.db")
    c = conn.cursor()
    c.execute("DELETE FROM chapters")  # Clear previous data
    for chapter in chapters:
        c.execute("INSERT INTO chapters VALUES (?, ?, ?, ?)", 
                  (chapter["chapter_number"], chapter["title"], chapter["start_page"], chapter["end_page"]))
    conn.commit()
    conn.close()

def get_chapters_from_db():
    conn = sqlite3.connect("chapters.db")
    c = conn.cursor()
    c.execute("SELECT * FROM chapters")
    chapters = c.fetchall()
    conn.close()
    return [{"chapter_number": row[0], "title": row[1], "start_page": row[2], "end_page": row[3]} for row in chapters]

# Extract text from PDF
def extract_text_from_pdf(pdf_bytes, max_pages=None):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = []
    for i, page in enumerate(doc):
        if max_pages and i >= max_pages:
            break
        text.append(page.get_text("text"))
    return "\n".join(text)

# Detect Table of Contents
def detect_toc(text):
    prompt = f"""
    Extract the Table of Contents or Index page which has the following content or headings in that page CHAPTER NUMBER,CONTENTS from the given text and return a valid JSON array.
    Each item should have:
    - "chapter_number": (string)
    - "title": (string)
    - "start_page": (integer)
    - "end_page": (integer)

    Example JSON Output:
    [
        {{"chapter_number": "1", "title": "Introduction", "start_page": 1, "end_page": 10}},
        {{"chapter_number": "2", "title": "Basics", "start_page": 11, "end_page": 20}}
    ]

    Text:
    {text}
    """
    response = llm.invoke(prompt)
    print("Groq Response:", response.content)  # Debugging step
    
    if not response.content.strip():
        raise ValueError("Groq returned an empty response")
    
    match = re.search(r"\[\s*\{.*?\}\s*\]", response.content, re.DOTALL)
    if not match:
        raise ValueError(f"Invalid JSON from Groq: {response.content}")
    
    extracted_json = match.group(0)  # Get the JSON part
    print("Extracted JSON:", extracted_json)  # Debugging step
    
    try:
        return json.loads(extracted_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse extracted JSON: {extracted_json}") from e

# Extract chapter text
def extract_chapter(text, start_page, end_page):
    start_page+=8
    end_page+=8
    pages = text.split("\n\n")
    return "\n".join(pages[start_page:end_page+1])

# Lesson Plan Generation
def get_lesson_plan(chapter_text, num_periods):
    prompt = f"""
    Create a structured lesson plan for the following chapter content, divided into {num_periods} periods.
    Ensure each period evenly covers the topics and provides key points in 2-3 lines.
    
    Chapter Content:
    {chapter_text}    
    Output format:
    | Period No | Topics to be Covered |
    |-----------|----------------------|
    """
    response = llm.invoke(prompt)
    return response.content.strip()

# Streamlit App
st.title("📚 PDF Knowledge Extractor with TOC Detection")
uploaded_file = st.file_uploader("Upload a PDF", type=["pdf"])

if uploaded_file:
    with st.spinner("Processing PDF..."):
        init_db()
        pdf_bytes = uploaded_file.read()  # Read once and reuse
        
        if not pdf_bytes:
            st.error("Failed to read PDF. Please upload a valid file.")
        else:
            text_15_pages = extract_text_from_pdf(pdf_bytes, max_pages=15)
            
            try:
                toc_data = detect_toc(text_15_pages)
                save_chapters_to_db(toc_data)
                st.success("✅ Table of Contents detected and stored!")
            except ValueError as e:
                st.error(f"Failed to extract TOC: {e}")
    
    chapters = get_chapters_from_db()
    chapter_options = {f"{ch['chapter_number']}: {ch['title']}" : ch for ch in chapters}
    selected_chapter = st.selectbox("Select a chapter", list(chapter_options.keys()))
    num_periods = st.number_input("Enter number of periods:", min_value=1, step=1, value=5)

    if st.button("Generate Lesson Plan"):
        chapter_info = chapter_options[selected_chapter]
        full_text = extract_text_from_pdf(pdf_bytes)
        chapter_text = extract_chapter(full_text, chapter_info["start_page"], chapter_info["end_page"])
        lesson_plan = get_lesson_plan(chapter_text, num_periods)
        st.subheader("📌 Lesson Plan")
        st.markdown(lesson_plan)
