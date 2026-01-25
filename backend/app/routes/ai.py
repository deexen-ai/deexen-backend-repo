from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
import time
import httpx
import os
import google.generativeai as genai

router = APIRouter()

# Initialize Gemini Model Global Instance
gemini_api_key = os.getenv("GEMINI_API_KEY", "")
gemini_model = None

if gemini_api_key:
    try:
        genai.configure(api_key=gemini_api_key)
        gemini_model = genai.GenerativeModel('gemini-2.5-flash')
        print("Gemini model initialized successfully.")
    except Exception as e:
        print(f"Failed to initialize Gemini: {e}")

class AnalyzeRequest(BaseModel):
    code: str
    mode: str
    model: str
    context: Optional[str] = "Deexen IDE"
    language: Optional[str] = "javascript"

class AnalyzeResponse(BaseModel):
    response:  str
    mode: str
    model: str
    tokens: Optional[int] = 0
    processingTime: Optional[float] = 0.0

@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_code(request: AnalyzeRequest):
    global gemini_model
    start_time = time.time()
    
    # Configuration
    api_base = os.getenv("AI_MODEL_API_BASE", "http://127.0.0.1:11434/v1")
    api_key = os.getenv("AI_MODEL_API_KEY", "none")
    model_name = request.model
    mode = request.mode
    
    # Force local Ollama for Magicoder
    if model_name.lower() == "magicoder":
        api_base = "http://127.0.0.1:11434/v1"
    
    
    # Construct prompt based on mode - LANGUAGE AGNOSTIC
    # We remove explicit {request.language} constraints to allow the model to infer from content
    # This handles "Python inside TSX file" scenarios correctly.
    
    system_prompt = "You are an expert coding assistant."
    user_prompt = f"Analyze the following code:\n\n{request.code}"
    
    if mode == "debug":
        system_prompt = "You are an expert code debugger. Find bugs and potential issues."
        user_prompt = f"Debug the following code and list issues with fixes:\n\n{request.code}"
    elif mode == "enhance":
        system_prompt = "You are a code quality expert. Suggest improvements for readability, performance, and structure."
        user_prompt = f"Enhance this code:\n\n{request.code}"
    elif mode == "expand":
        system_prompt = "You are a software architect. Suggest feature expansions and scalability improvements."
        user_prompt = f"Propose feature expansions for this code:\n\n{request.code}"
    elif mode == "teaching":
        system_prompt = "You are a socratic teacher. Guide the user to find the answer without giving it directly."
        user_prompt = f"Teach me about the issues in this code using hints:\n\n{request.code}"
    elif mode == "livefix":
        system_prompt = "You are a real-time code monitor. Provide brief, instant fixes."
        user_prompt = f"Quickly fix any critical issues in this snippet:\n\n{request.code}"

    # Map frontend model IDs to local Ollama model names
    ollama_model_map = {
        "llama3-8b": "llama3",
        "magicoder": "magicoder",
    }
    
    # Use mapped name if available, otherwise use original
    target_model = ollama_model_map.get(model_name, model_name)


    try:
        if model_name.lower().startswith("gemini"):
             if not gemini_model:
                 # Attempt lazy init if safe
                 key = os.getenv("GEMINI_API_KEY")
                 if key:
                     genai.configure(api_key=key)
                     # global gemini_model # Removed: declared at top of function
                     # For simplicity, we create a local instance if global failed, but normally global runs on import
                     gemini_model_local = genai.GenerativeModel('gemini-2.5-flash')
                     gemini_prompt = f"{system_prompt}\n\n{user_prompt}"
                     response = await gemini_model_local.generate_content_async(gemini_prompt)
                     response_text = response.text
                 else:
                     raise Exception("GEMINI_API_KEY not set or model initialization failed")
             else:
                 gemini_prompt = f"{system_prompt}\n\n{user_prompt}"
                 response = await gemini_model.generate_content_async(gemini_prompt)
                 response_text = response.text


        else:
            # Standard OpenAI/Ollama Format
            print(f"DEBUG: Connecting to {api_base}/chat/completions")
            print(f"DEBUG: Model: {target_model}")
            
            # Increase timeout and add retries if needed
            timeout_config = httpx.Timeout(60.0, connect=10.0)
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                try:
                    response = await client.post(
                        f"{api_base}/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        json={
                            "model": target_model,
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt}
                            ],
                            "temperature": 0.2 if mode in ["debug", "livefix"] else 0.7,
                        }
                    )
                    
                    if response.status_code != 200:
                        print(f"AI API Error: {response.text}")
                        # If model not found, try to give a helpful specific error
                        if response.status_code == 404:
                            raise Exception(f"Model '{target_model}' not found on server.")
                        raise Exception(f"API Error: {response.status_code}")

                    data = response.json()
                    response_text = data["choices"][0]["message"]["content"]
                except (httpx.ConnectError, httpx.ReadTimeout) as e:
                     print(f"DEBUG: Exception type: {type(e)}")
                     print(f"DEBUG: Exception details: {e}")
                     raise Exception(f"Connection Failed: {str(e)}")

            
    except Exception as e:
        if "Connection Failed" in str(e) or "not found" in str(e):
             print(f"AI Model Status: {str(e)} (Falling back to simulation)")
        else:
             import traceback
             traceback.print_exc()
             print(f"AI Connection Failed: {repr(e)}")
        
        # Graceful fallback to simulation logic
        if "magicoder" in model_name.lower() or "wizard" in model_name.lower():
            response_text = f"**[Offline Simulation Mode]**\n(Could not connect to model at {api_base}. Error: {e})\nDEBUG: Model={model_name}, Target={target_model}, Base={api_base}\n\nAnalyzed using {model_name} in {mode} mode.\n\n"
            if mode == "debug":
                response_text += "Found potential issues in the syntax. Recommend checking line 5 for null safety."
            elif mode == "enhance":
                response_text += "Suggesting refactor to use async/await for better readability."
            elif mode == "expand":
                response_text += "Proposed expansion: Implement a caching layer logic and add retry mechanism with exponential backoff."
            elif mode == "teaching":
                response_text += "Let's analyze this together. What happens if the input is null? Hint: Check boundary conditions."
            elif mode == "livefix":
                response_text += "Live Monitor: No critical errors found. Suggesting type refinement on line 12."
            else:
                response_text += "Analysis complete. The code looks standard but could be optimized for performance."
        else:
             response_text = f"**[Offline Mode]** Connection failed. Please check your API configuration."

    processing_time = round(time.time() - start_time, 2)
    
    return AnalyzeResponse(
        response=response_text,
        mode=mode,
        model=model_name,
        tokens=len(response_text) // 4,
        processingTime=processing_time
    )
