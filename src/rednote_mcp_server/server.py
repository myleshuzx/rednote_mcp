# -*- coding: utf-8 -*-
import asyncio
import uvicorn
from pathlib import Path
import sys
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, HttpUrl

# Ensure src directory is in Python path
# current_dir = Path(__file__).parent
# sys.path.append(str(current_dir / 'src'))

from mcp.server.fastmcp import FastMCP
# from fastmcp import ToolContext
from pydantic import BaseModel, Field, HttpUrl

from browser_handler import BrowserHandler
# from models import SearchNoteParams, LoginParams # Removed GetNoteContentParams

import os



# 初始化mcp服务
mcp = FastMCP("hello-mcp-server")

user_data_dir_to_use = os.getenv("DEFAULT_USER_DATA_DIR") 
if user_data_dir_to_use is None:
    user_data_dir_to_use = "C:\\Users\\myles\\AppData\\Local\\Google\\Chrome\\User Data\\Default"

browser_handler = BrowserHandler(user_data_dir=user_data_dir_to_use)


@mcp.tool(
    name="search_note",
    description="Search for notes on Xiaohongshu based on keywords."
)
async def search_note_tool(keywords: str = Field(description="keywords"), limit: int = Field(default=10, description="number of results in return"), headless: bool = Field(default=False, description="whether to run browser in headless mode, False: use GUI browser, True: not use GUI browser"))-> Dict[str, Any]:
    """Searches for notes based on keywords."""
    try:
        results = await browser_handler.search_notes(
            keywords=keywords,
            limit=limit,
            headless=headless
        )
        return {"results": results}
    finally:
        # Decide if browser should be closed after each search
        # await browser_handler.close() # Uncomment if browser should close after this operation
        pass


def run():
    mcp.run(transport="stdio")


if __name__ == "__main__":
   run()