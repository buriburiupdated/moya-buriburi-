from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import asyncio
import uvicorn
from asyncio import CancelledError

from moya.agents.openai_agent import OpenAIAgent
from moya.memory.in_memory_repository import InMemoryRepository
from moya.tools.tool_registry import ToolRegistry
from moya.tools.memory_tool import MemoryTool


app = FastAPI()

def setup_agent():
    """Set up OpenAI agent with memory capabilities."""
    # Set up memory components
    memory_repo = InMemoryRepository()
    memory_tool = MemoryTool(memory_repository=memory_repo)
    tool_registry = ToolRegistry()
    tool_registry.register_tool(memory_tool)

    # Create and setup agent
    agent = OpenAIAgent(
        agent_name="remote_chat_agent",
        description="A remote chat agent with memory",
        model_name="gpt-4o",
        tool_registry=tool_registry,
        system_prompt="""You are a witty AI assistant specialized in telling jokes.
        Always respond with humor and keep the mood light and entertaining.
        If asked for a joke, make sure it's appropriate and family-friendly.
        If asked for anything else, try to include some humor in your response."""
    )
    agent.setup()
    return agent

# Initialize agent at startup
agent = setup_agent()

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "agent": agent.agent_name}

@app.post("/chat")
async def chat(request: Request):
    """Handle normal chat requests using OpenAI agent."""
    data = await request.json()
    message = data['message']
    thread_id = data.get('thread_id', 'default_thread')
    
    # Store user message if memory tool is available
    if agent.tool_registry:
        try:
            agent.call_tool(
                tool_name="MemoryTool",
                method_name="store_message",
                thread_id=thread_id,
                sender="user",
                content=message
            )
        except Exception as e:
            print(f"Error storing user message: {e}")

    # Get response from agent
    response = agent.handle_message(message, thread_id=thread_id)
    
    # Store agent response if memory tool is available
    if agent.tool_registry:
        try:
            agent.call_tool(
                tool_name="MemoryTool",
                method_name="store_message",
                thread_id=thread_id,
                sender=agent.agent_name,
                content=response
            )
        except Exception as e:
            print(f"Error storing agent response: {e}")
    
    return {"response": response}

async def stream_response(message: str, thread_id: str):
    """Stream response from OpenAI agent."""
    response_text = ""
    current_text = ""

    async def send_chunk(text: str):
        """Helper to format and send SSE chunks."""
        return f"data: {text}\n\n"

    try:
        # Store user message
        if agent.tool_registry:
            agent.call_tool(
                tool_name="MemoryTool",
                method_name="store_message",
                thread_id=thread_id,
                sender="user",
                content=message
            )
        
        # Stream response
        for chunk in agent.handle_message_stream(message, thread_id=thread_id):
            if chunk:
                # Add chunk to current text
                current_text += chunk
                
                # Find word boundaries
                words = current_text.split()
                
                if len(words) > 1:  # If we have at least one complete word
                    # Keep the last word in case it's incomplete
                    text_to_send = ' '.join(words[:-1]) + ' '
                    current_text = words[-1]
                    
                    response_text += text_to_send
                    yield await send_chunk(text_to_send)
                
                await asyncio.sleep(0.01)

        # Send any remaining text
        if current_text:
            response_text += current_text
            yield await send_chunk(current_text)

        # Store complete response
        if agent.tool_registry and response_text:
            agent.call_tool(
                tool_name="MemoryTool",
                method_name="store_message",
                thread_id=thread_id,
                sender=agent.agent_name,
                content=response_text
            )
            
    except CancelledError:
        if response_text:  # Only log if we actually got some response
            print(f"Client disconnected, stored partial response of length: {len(response_text)}")
        return
        
    except Exception as e:
        error_msg = f"[Error: {str(e)}]"
        yield await send_chunk(error_msg)

@app.post("/chat/stream")
async def chat_stream(request: Request):
    """Handle streaming chat requests using OpenAI agent."""
    data = await request.json()
    message = data['message']
    thread_id = data.get('thread_id', 'default_thread')
    
    return StreamingResponse(
        stream_response(message, thread_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "text/event-stream",
            "Transfer-Encoding": "chunked"
        }
    )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
