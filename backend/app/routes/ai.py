from fastapi import APIRouter, HTTPException, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Optional
import time
import httpx
import os
import google.generativeai as genai
import json
import asyncio

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
    skillLevel: Optional[str] = "intermediate"
    role: Optional[str] = "user"

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
    api_base = os.getenv("AI_MODEL_API_BASE", "https://openrouter.ai/api/v1")
    api_key = os.getenv("OPENROUTER_API_KEY", os.getenv("AI_MODEL_API_KEY", "none"))
    model_name = request.model
    mode = request.mode
    
    # Optional override for local models
    if model_name.lower() == "magicoder" and api_key == "none":
        api_base = "http://127.0.0.1:11434/v1"
    # Construct prompt based on mode - LANGUAGE AGNOSTIC
    # We remove explicit {request.language} constraints to allow the model to infer from content
    # This handles "Python inside TSX file" scenarios correctly.
    
    skill_level = request.skillLevel.lower() if request.skillLevel else "intermediate"
    user_role = request.role.lower() if request.role else "user"
    
    # Establish Persona Base
    persona = "You are an expert coding assistant."
    if user_role == "student" or skill_level == "beginner":
        persona = (
            "You are an Advanced Debugging Assistant designed specifically for students and beginners. "
            "Your role is to help users understand and fix errors, not just provide answers. "
            "Explain errors in simple, beginner-friendly language. Use real-world analogies. "
            "Provide step-by-step fixes, explain why it happens, and do not interrupt unnecessarily."
        )
    elif user_role == "professional" or skill_level == "advanced":
        persona = (
            "You are an Advanced Coding Assistant for professionals. "
            "Provide concise answers. Focus on architecture, speed, and optimization. "
            "Provide code snippets only without long explanations unless explicitly requested."
        )

    # Establish Mode Goal
    mode_instructions = ""
    user_prompt = f"Analyze the following code:\n\n{request.code}"
    
    if mode == "debug":
        mode_instructions = "Analyze the code to find bugs, logical errors, and best practice violations. List issues and fixes."
        user_prompt = f"Debug the following code:\n\n{request.code}"
    elif mode == "enhance":
        mode_instructions = "Suggest improvements for readability, performance, structure, and caching."
        user_prompt = f"Enhance this code:\n\n{request.code}"
    elif mode == "expand":
        mode_instructions = "Suggest feature expansions, scalability improvements, and new system modules."
        user_prompt = f"Propose feature expansions for this code:\n\n{request.code}"
    elif mode == "teaching":
        mode_instructions = "Act as a socratic teacher. DO NOT provide the immediate solution. Guide the user with hints."
        user_prompt = f"Teach me about the issues in this code using hints:\n\n{request.code}"
    elif mode == "livefix":
        mode_instructions = "Act as a real-time smart monitor. Provide only critical, brief, instant fixes."
        user_prompt = f"Quickly fix any critical issues in this snippet:\n\n{request.code}"
        
    system_prompt = f"{persona}\n\nTask Instructions:\n{mode_instructions}"

    # Map frontend model IDs to exact OpenRouter Free endpoints
    model_map = {
        "gemini-free": "google/gemma-3-27b-it:free",
        "llama-70b-free": "meta-llama/llama-3.3-70b-instruct:free",
        "llama-8b-free": "meta-llama/llama-3.2-3b-instruct:free",
        "deepseek-free": "openrouter/free",
        "mistral-nemo-free": "mistralai/mistral-small-3.1-24b-instruct:free",
        "qwen-coder-free": "qwen/qwen3-coder:free",
        "magicoder": "qwen/qwen3-coder:free", # fallback if openrouter is used
    }
    
    # Use mapped name if available, otherwise use original
    target_model = model_map.get(model_name, model_name)
    
    # Disable native gemini logic and route everything through OpenRouter if api key is present
    use_native_gemini = False
    try:
        if use_native_gemini and model_name.lower().startswith("gemini"):
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
                                {"role": "user", "content": f"{system_prompt}\n\n{user_prompt}"}
                            ] if "gemma" in target_model.lower() else [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt}
                            ],
                            "temperature": 0.2 if mode in ["debug", "livefix"] else 0.7,
                            "max_tokens": 1500,
                        }
                    )
                    
                    if response.status_code != 200:
                        print(f"AI API Error: {response.text}")
                        if response.status_code == 429 and target_model != "openrouter/free":
                            print(f"Model {target_model} is rate limited upstream. Falling back to Groq API.")
                            target_model = "llama-3.3-70b-versatile"
                            fallback_api_base = "https://api.groq.com/openai/v1"
                            fallback_api_key = "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"
                            
                            # Retry request with fallback model on Groq
                            response = await client.post(
                                f"{fallback_api_base}/chat/completions",
                                headers={"Authorization": f"Bearer {fallback_api_key}"},
                                json={
                                    "model": target_model,
                                    "messages": [
                                        {"role": "system", "content": system_prompt},
                                        {"role": "user", "content": user_prompt}
                                    ],
                                    "temperature": 0.2 if mode in ["debug", "livefix"] else 0.7,
                                    "max_tokens": 1500,
                                }
                            )
                            if response.status_code != 200:
                                print(f"Fallback AI API Error: {response.text}")
                                raise Exception(f"API Error: {response.status_code}")
                        else:
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

@router.websocket("/ws/livefix")
async def websocket_livefix(websocket: WebSocket):
    await websocket.accept()
    
    # We use Groq specifically for LiveFix as per user request
    api_base = "https://api.groq.com/openai/v1"
    api_key = "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"
    timeout_config = httpx.Timeout(60.0, connect=10.0)
    
    try:
        while True:
            # Wait for messages from the client
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
                code = payload.get("code", "")
                language = payload.get("language", "javascript")
                file_name = payload.get("file_name", "Unknown")
                cursor_pos = payload.get("cursor_pos", None)
                mode = payload.get("mode", "livefix")
                model_name = "llama-3.3-70b-versatile" # Default fast groq model
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON format payload"})
                continue
                
            if not code:
                continue
                
            mode_instruction = "Analyze the user's code for bugs, errors, and optimizations."
            mode_instruction = "Analyze the user's code for bugs, errors, and optimizations."
            if mode == "debug":
                mode_instruction = "Focus STRICTLY on finding bugs, syntax errors, and logical issues. Ignore incomplete typing."
            elif mode == "enhance":
                mode_instruction = "Focus STRICTLY on code quality, readability, performance, and best practices. Do NOT suggest docstrings or type hints."
            elif mode == "expand":
                mode_instruction = "Focus on identifying areas where features are missing or could be expanded."
            elif mode == "teaching":
                mode_instruction = "Focus on pointing out issues but DO NOT provide the exact code fix. Instead, provide hints or questions in the 'message' and leave 'suggestion' as an empty string."
            elif mode == "livefix":
                mode_instruction = "Act as a silent monitor. ONLY flag critical syntax errors, obvious logical bugs, or severe anti-patterns. IGNORE missing docstrings, type hints, or incomplete typing."
            
            persona = (
                f"You are an expert Real-Time AI Coding Advisor ({mode} mode). "
                f"{mode_instruction} "
                f"CRITICAL: ALL 'suggestion' values MUST be written strictly in {language.upper()} syntax. DO NOT provide Javascript fixes for Python files. "
                "Provide ONLY valid JSON as a response, NO Markdown formatting, NO explanations. "
                "You MUST NOT wrap the JSON in ```json blocks. "
                "The JSON must be an array of objects. Each object must have: "
                "'line' (integer corresponding to the 1-indexed line number in the source), "
                "'severity' (string: 'error', 'warning', or 'info'), "
                "'message' (string: concise description of the issue), "
                "and 'suggestion' (string: the exact replacement code for the line/lines, or empty if teaching mode). "
                "If there are no critical issues relevant to this mode, you MUST return an empty array []. "
                "Do NOT include any text outside the JSON array."
            )
            
            user_prompt = f"File Context: {file_name}\nLanguage: {language}\nCode:\n{code}\n"
            if cursor_pos:
                 user_prompt += f"Cursor is at line: {cursor_pos.get('lineNumber')}, column: {cursor_pos.get('column')}\n"
                 
            system_prompt = persona
            
            # Groq model mapping
            target_model = "llama-3.3-70b-versatile"
            
            await websocket.send_json({"type": "status", "message": "Analyzing..."})
            
            # Start streaming response
            try:
                async with httpx.AsyncClient(timeout=timeout_config) as client:
                    async with client.stream(
                        "POST", 
                        f"{api_base}/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        json={
                            "model": target_model,
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt}
                            ],
                            "temperature": 0.1,
                            "max_tokens": 1000,
                            "stream": True # Enable streaming
                        }
                    ) as response:
                        if response.status_code != 200:
                            error_text = await response.aread()
                            print(f"WS API Error: {error_text}")
                            if response.status_code == 429:
                                await websocket.send_json({"type": "error", "message": "API Rate Limit Exceeded. Please wait a moment."})
                            else:
                                await websocket.send_json({"type": "error", "message": f"API Error {response.status_code}"})
                            continue
                            
                        full_response = ""
                        async for chunk in response.aiter_lines():
                            if chunk.startswith("data: "):
                                data_str = chunk[6:]
                                if data_str == "[DONE]":
                                    break
                                try:
                                    json_data = json.loads(data_str)
                                    delta = json_data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                                    if delta:
                                        full_response += delta
                                        await websocket.send_json({"type": "chunk", "text": delta})
                                except json.JSONDecodeError:
                                    pass
                                    
                        await websocket.send_json({"type": "done", "full_text": full_response})
                        
            except Exception as e:
                print(f"WS Stream Exception: {e}")
                await websocket.send_json({"type": "error", "message": f"Stream failed: {str(e)}"})
                
    except WebSocketDisconnect:
        print("LiveFix Client disconnected")
