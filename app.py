import streamlit as st
import fitz
import os
import getpass
import sqlite3
import time
import re
import json
from langchain.chat_models import init_chat_model

# Ensure API Key
if not os.environ.get("GROQ_API_KEY"):
    os.environ["GROQ_API_KEY"] = getpass.getpass("Enter API key for Groq: ")

llm = init_chat_model("llama-3.3-70b-versatile", model_provider="groq")

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
        if None not in (chapter["start_page"], chapter["end_page"]):
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
    return [{"chapter_number": row[0], "title": row[1], 
             "start_page": int(row[2]) if row[2] else None,
             "end_page": int(row[3]) if row[3] else None} for row in chapters]

def get_first_chapter_start_page():
    chapters = get_chapters_from_db()
    return chapters[0]["start_page"] if chapters else 1

def extract_text_from_pdf(pdf_bytes, start_page, end_page):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = []
    for i, page in enumerate(doc):
        if start_page <= i + 1 <= end_page:
            text.append(page.get_text("text"))
    return "\n".join(text) if text else ""

def detect_toc(text):
    prompt = f'''
    Extract the structured Table of Contents from the given text.
    Identify the chapter numbers, titles, and their respective start and end pages.
    Ensure that both structured and unstructured formats are considered and return JSON output.

    Example JSON Output:
    [
        {{"chapter_number": "1", "title": "Introduction", "start_page": 1, "end_page": 10}},
        {{"chapter_number": "2", "title": "Basics", "start_page": 11, "end_page": 20}}
    ]

    Text:
    {text}
    '''
    response = llm.invoke(prompt)
    if not response or not response.content.strip():
        st.error("Error: Empty response from LLM while detecting TOC.")
        return []
    
    try:
        match = re.search(r"\[.*\]", response.content, re.DOTALL)
        if match:
            extracted_json = match.group(0)
            return json.loads(extracted_json)
        else:
            st.error("Error: No valid JSON found in response.")
            return []
    except json.JSONDecodeError:
        st.error("Error: Invalid JSON response from LLM while detecting TOC.")
        return []

def extract_chapter(pdf_bytes, start_page, end_page, first_chapter_first_page):
    first_chapter_start_page_doc = get_first_chapter_start_page()
    adjustment = first_chapter_first_page-first_chapter_start_page_doc
    start_page += adjustment
    end_page += adjustment
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    font_sizes = {}
    headings = []
    total_words = 0
    max_words = 2000
    print("Start page:",start_page)
    print("End page:",end_page)
    
    for i, page in enumerate(doc):
        if start_page <= i + 1 <= end_page:
            blocks = page.get_text("dict")["blocks"]
            for block in blocks:
                if "lines" in block:
                    for line in block["lines"]:
                        for span in line["spans"]:
                            font_sizes[span["size"]] = font_sizes.get(span["size"], 0) + len(span["text"].split())
    
    sorted_sizes = sorted(font_sizes.items(), key=lambda x: -x[0])
    
    selected_sizes = []
    for size, words in sorted_sizes:
        if total_words + words <= max_words:
            selected_sizes.append(size)
            total_words += words
        else:
            break
    
    for i, page in enumerate(doc):
        if start_page <= i + 1 <= end_page:
            blocks = page.get_text("dict")["blocks"]
            for block in blocks:
                if "lines" in block:
                    for line in block["lines"]:
                        for span in line["spans"]:
                            if span["size"] in selected_sizes or "color" in span or "bold" in span:
                                headings.append(span["text"])
    
    return "\n".join(headings) if headings else ""

def get_lesson_plan(chapter_text, num_periods, class_level):
    max_chunk_size = 2000  # Limit per request to avoid token overflow
    words = chapter_text.split()  # Split text into words
    chunks = [" ".join(words[i:i+max_chunk_size]) for i in range(0, len(words), max_chunk_size)]

    extracted_subtopics = []

    # Step 1: Extract key subtopics from each chunk
    for i, chunk in enumerate(chunks):
        prompt = '''
        Identify the most important subtopics from the following chapter content.
        Ensure each subtopic is concise and meaningful.

        Chapter Content:
        {}

        Output format:
        - Subtopic 1
        - Subtopic 2
        - Subtopic 3
        '''.format(chunk)

        response = llm.invoke(prompt)
        subtopics = response.content.strip().split("\n")
        extracted_subtopics.extend(subtopics)

    # Step 2: Generate final lesson plan using extracted subtopics
    final_prompt = '''
    Create a structured lesson plan for the following chapter content, divided into {} periods, for class level {}.
    Ensure each period evenly covers the topics and provides key points in 2-3 lines.

    Key Subtopics:
    {}

    Output format:
    | Period No | Topics to be Covered |
    |-----------|----------------------|
    '''.format(num_periods, class_level, "\n".join(extracted_subtopics))

    response = llm.invoke(final_prompt)
    return response.content.strip()




st.title("📚 PDF Knowledge Extractor")
status_placeholder = st.empty()
uploaded_file = st.file_uploader("Upload a PDF", type=["pdf"])

if uploaded_file:
    init_db()
    pdf_bytes = uploaded_file.read()
    
    index_start_page = st.number_input("Enter Index Start Page:", min_value=1, step=1, value=1)
    index_end_page = st.number_input("Enter Index End Page:", min_value=1, step=1, value=1)
    
    if st.button("Detect the Index"):
        status_placeholder.text("Processing index pages...")
        index_text = extract_text_from_pdf(pdf_bytes, index_start_page, index_end_page)
        chapters = detect_toc(index_text)
        if chapters:
            save_chapters_to_db(chapters)
        else:
            st.error("No chapters found in the extracted text.")
    
    chapters = get_chapters_from_db()
    if chapters:
        chapter_options = {f"{ch['chapter_number']}: {ch['title']} (Pg {ch['start_page']} - {ch['end_page']})": ch for ch in chapters}
        selected_chapter = st.selectbox("Select a chapter", list(chapter_options.keys()))
        num_periods = st.number_input("Enter number of periods:", min_value=1, step=1, value=5)
        first_chapter_first_page = st.number_input("Enter the first chapter's first page:", min_value=1, step=1, value=10)
        
        class_level = st.text_input("Enter the class level (e.g., 7th Grade):")  # New input field for class level
        
        if st.button("Generate Lesson Plan"):
            if class_level:
                status_placeholder.text("Extracting chapter for lesson plan...")
                chapter_info = chapter_options[selected_chapter]
                chapter_text = extract_chapter(pdf_bytes, chapter_info["start_page"], chapter_info["end_page"], first_chapter_first_page)
                lesson_plan = get_lesson_plan(chapter_text, num_periods, class_level)  # Pass class level to the function
                st.success("✅ Lesson plan generated!")
                st.markdown(lesson_plan)
            else:
                st.error("Please enter the class level.")
