import asyncio # Added for asynchronous operations
import os
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
import pytesseract
from PIL import Image
import requests
# import time # Replaced with asyncio

from playwright.async_api import async_playwright, BrowserContext, Page, Playwright # Changed to async_api

# Global definitions for persistent context
STORAGE_STATE_FILE = "playwright_state.json"
# USER_DATA_DIR = r'C:\Users\myles\AppData\Local\Google\Chrome\User Data\Default'
# USER_DATA_DIR = "C:\\Users\\myles\\AppData\\Local\\Google\\Chrome\\User Data\\Default"
# USER_DATA_DIR = r'C:\Users\myles\AppData\Local\Google\Chrome\User Data'

class BrowserHandler:
    def __init__(self, user_data_dir, enable_logging: bool = False):
        self.user_data_dir = user_data_dir
        self.storage_state_file_path = Path(STORAGE_STATE_FILE).resolve()
        self.playwright: Optional[Playwright] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.logged_in_successfully = False

        self.logging_enabled = enable_logging
        self.log_file_path: Optional[Path] = None
        self.log_file_handler = None
        self.log_dir = Path(__file__).parent.parent / 'log'
        if self.logging_enabled:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"IMPORTANT: 请在运行此脚本前关闭所有 Chrome 浏览器窗口和后台进程，特别是当使用现有配置文件时。")

    async def _setup_logging(self): # Added async
        if not self.logging_enabled:
            if self.log_file_handler:
                try: self.log_file_handler.close() # sync file op
                except Exception: pass
                self.log_file_handler = None
            self.log_file_path = None
            return

        if self.log_file_handler: # Close previous if any
            try: self.log_file_handler.close() # sync file op
            except Exception as e: print(f"关闭旧日志文件时出错: {e}")
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file_path = self.log_dir / f"log_{timestamp}.txt"
        try:
            self.log_file_handler = open(self.log_file_path, 'w', encoding='utf-8') # sync file op
            print(f"日志记录到: {self.log_file_path}")
        except Exception as e:
            print(f"打开日志文件 {self.log_file_path} 时出错: {e}")
            self.log_file_handler = None

    async def _save_session_state(self): # Added async
        if self.context and self.logged_in_successfully: # Check if context exists and logged in
            try:
                await self.context.storage_state(path=str(self.storage_state_file_path))
                print(f"会话状态已成功保存到 {self.storage_state_file_path}")
                if self.logging_enabled and self.log_file_handler:
                    self.log_file_handler.write(f"[{datetime.now()}] Session state saved to {self.storage_state_file_path}\n")
                    self.log_file_handler.flush()
            except Exception as e_save_state: # This will catch errors if context is closed or other issues
                print(f"保存会话状态到 {self.storage_state_file_path} 失败: {e_save_state}")
                if self.logging_enabled and self.log_file_handler:
                    self.log_file_handler.write(f"[{datetime.now()}] FAILED to save session state: {e_save_state}\n")
                    self.log_file_handler.flush()
        elif self.context and not self.logged_in_successfully:
            print("登录不成功或未验证，跳过保存会话状态。")
        elif not self.context:
            print("浏览器上下文不存在，无法保存会话状态。")
        # The case where self.context exists but is closed will be handled by the exception above.

    async def _get_or_create_persistent_context(self, headless: bool = True) -> BrowserContext: # Added async, headless param
        if self.context: # Check if context object exists
            try:
                # Attempt a benign operation to check if context is alive and has a usable page
                if self.context.pages and not self.context.pages[0].is_closed(): # Page's is_closed is fine
                    await self.context.pages[0].title() # Check responsiveness
                    print("复用现有的持久化浏览器上下文。")
                    return self.context
                else: # No pages or first page is closed
                    print("现有上下文的页面已关闭/不存在，将关闭此上下文并创建新的。")
                    await self.context.close() # Attempt to close the existing context
                    self.context = None
                    self.page = None # Also clear associated page
            except Exception as e: # Catches errors from .title() or .close() if context was already dead/unresponsive
                print(f"现有上下文无法使用 ({e})，将关闭并创建新的持久化上下文。")
                if self.context: # Ensure context is not None before trying to close again
                    try: await self.context.close()
                    except Exception: pass # Ignore errors during this cleanup close
                self.context = None
                self.page = None

        if not self.playwright:
            self.playwright = await async_playwright().start() # Added await, changed to async_playwright

        await self._setup_logging() # Added await

        print(f"检查用户数据目录: {self.user_data_dir}")
        if not os.path.exists(self.user_data_dir):
            print(f"警告：Chrome 用户数据目录 {self.user_data_dir} 可能不存在或无法访问。Playwright 可能会尝试创建它，但这通常用于新配置文件。")

        effective_storage_state = str(self.storage_state_file_path) if self.storage_state_file_path.exists() else None
        if effective_storage_state:
            print(f"尝试从 {self.storage_state_file_path} 加载会话...")
        else:
            print(f"未找到会话文件 {self.storage_state_file_path}，将启动全新会话。")

        try:
            self.context = await self.playwright.chromium.launch_persistent_context( # Added await
                self.user_data_dir,
                headless=headless, # Use passed headless argument
                # headless=False,
                channel="chrome",
                slow_mo=500,
                accept_downloads=True,
                args=["--disable-blink-features=AutomationControlled"]
            )
            print("持久化浏览器上下文启动成功。")
            self.logged_in_successfully = False # Reset, will be verified
            return self.context
        except Exception as e_launch:
            print(f"启动持久化浏览器上下文失败: {e_launch}")
            if self.context: # If launch partially succeeded then failed, self.context might exist
                try: await self.context.close()
                except Exception: pass # Ignore errors during cleanup
            self.context = None
            raise

    async def initialize_and_get_page(self, headless: bool = True) -> Page: # Added async, headless param
        self.context = await self._get_or_create_persistent_context(headless=headless) # Added await, pass headless

        if self.context.pages:
            self.page = self.context.pages[0]
            try:
                await self.page.title() # Added await, check responsiveness
                print(f"复用来自持久化上下文的现有页面: {self.page.url}")
            except Exception:
                print("现有页面无响应或已关闭，创建新页面。")
                self.page = await self.context.new_page() # Added await
        else:
            print("持久化上下文中没有现有页面，创建新页面。")
            self.page = await self.context.new_page() # Added await
        
        print(f"正在验证会话 (来自 {self.storage_state_file_path if self.storage_state_file_path.exists() else '新会话'})...")
        try:
            await self.page.goto("https://www.xiaohongshu.com/explore", timeout=30000, wait_until="domcontentloaded") # Added await
        except Exception as e_goto:
            print(f"导航到 explore 页面失败: {e_goto}. 可能需要手动干预或检查网络。")

        await asyncio.sleep(3) # Wait for page to settle
        current_url = self.page.url # Get current URL for logging

        # Primary check: Look for the "我" profile element indicating successful login
        my_profile_element = await self.page.query_selector("span.channel:has-text('我')")

        if my_profile_element:
            print(f"检测到 '我' 元素，表明已通过持久化会话自动登录或之前已登录。当前 URL: {current_url}")
            self.logged_in_successfully = True
        else:
            # "我" element not found, now check for login prompt
            print(f"未检测到 '我' 元素。当前 URL: {current_url}。检查是否存在登录提示...")
            login_reason_element = await self.page.query_selector(".login-reason")
            if login_reason_element:
                print(f"检测到登录提示元素 (class='login-reason')，表明需要登录。当前 URL: {current_url}")
                self.logged_in_successfully = False
                if self.storage_state_file_path.exists():
                    try:
                        print(f"由于会话无效或需要验证，尝试删除旧的会话文件: {self.storage_state_file_path}")
                        os.remove(self.storage_state_file_path) # sync file op
                        print(f"已删除无效的会话文件: {self.storage_state_file_path}")
                    except OSError as e_remove:
                        print(f"删除无效会话文件 {self.storage_state_file_path} 失败: {e_remove}")
                
                print("请在浏览器窗口中完成小红书的登录操作。脚本将等待最多40秒。")
                
                login_check_attempts = 0
                max_login_wait_seconds = 60
                login_successful_within_timeout = False
                while login_check_attempts < max_login_wait_seconds:
                    current_url_after_manual_login = self.page.url # Re-check URL each iteration
                    my_profile_element_after_manual_login = await self.page.query_selector("span.channel:has-text('我')")
                    if my_profile_element_after_manual_login:
                        print(f"在尝试 {login_check_attempts + 1} 秒后检测到 '我' 元素。当前 URL: {current_url_after_manual_login}。假定登录成功。")
                        self.logged_in_successfully = True
                        login_successful_within_timeout = True
                        break
                    else:
                        print(f"等待手动登录... ({login_check_attempts + 1}/{max_login_wait_seconds} 秒) URL: {current_url_after_manual_login}")
                        await asyncio.sleep(1) # Wait 1 second before next check
                    login_check_attempts += 1
                
                if not login_successful_within_timeout:
                    current_url_after_timeout = self.page.url # Get final URL after timeout
                    # Check if still on login page as a fallback
                    if "login" in current_url_after_timeout.lower() or "passport" in current_url_after_timeout.lower():
                        print(f"警告：{max_login_wait_seconds}秒超时后仍未检测到 '我' 元素，且 URL ({current_url_after_timeout}) 暗示仍在登录页。登录失败。")
                        self.logged_in_successfully = False
                    else:
                        print(f"警告：{max_login_wait_seconds}秒超时后仍未检测到 '我' 元素，但 URL ({current_url_after_timeout}) 不是标准登录页。状态不明确，假定登录失败以策安全。")
                        self.logged_in_successfully = False # Safer to assume false
            else:
                # No "我" element AND no "login-reason" element.
                # This could mean the page is loaded but not fully, or an unexpected state.
                print(f"未检测到 '我' 元素，也未检测到明确的登录提示。当前 URL: {current_url}。假定未登录或会话无效。")
                self.logged_in_successfully = False # Default to false if neither specific condition is met

        if self.logged_in_successfully:
            await self._save_session_state() # Added await
        
        return self.page

    async def login(self, headless: bool = False) -> None: # Added async, headless param
        print("正在初始化会话并检查登录状态...")
        try:
            await self.initialize_and_get_page(headless=headless) # Added await, pass headless
            if self.logged_in_successfully:
                print("登录成功并已保存/验证会话状态。")
            else:
                print("登录似乎未成功完成。")
        except Exception as e:
            print(f"登录过程中发生错误: {e}")

    async def _ensure_logged_in_page(self, headless: bool = True) -> Page: # Added async, headless param
        # Check if current page and context seem valid and logged in
        if self.page and not self.page.is_closed() and \
           self.context and self.logged_in_successfully: # Removed 'not self.context.is_closed()'
            try:
                # Verify page is responsive and not on a login screen
                current_url = self.page.url # This might fail if page is detached
                if "login" in current_url.lower() or "passport" in current_url.lower():
                    print("会话似乎已失效（重定向到登录页），将重新初始化...")
                    self.logged_in_successfully = False # Mark as not logged in
                    # Don't return, fall through to re-initialize
                else:
                    print(f"当前会话有效，页面 URL: {current_url}")
                    return self.page # Current page is good
            except Exception as e:
                print(f"检查现有页面/会话时出错 ({e})，将重新初始化会话...")
                # Aggressively clean up to force re-initialization
                self.page = None
                if self.context:
                    try: await self.context.close()
                    except Exception: pass # Ignore errors on close
                self.context = None
                self.logged_in_successfully = False
        
        # If initial checks fail, or if state was reset due to an error:
        print("页面/会话无效或未初始化，或登录状态失效。调用 initialize_and_get_page() 进行刷新。")
        return await self.initialize_and_get_page(headless=headless) # Added await, pass headless

    async def search_notes(self, keywords: str, limit: int = 10, headless: bool = False, image_ocr: bool = False) -> List[Dict[str, Any]]: # Added async, headless param
        # page = await self._ensure_logged_in_page(headless=headless) # Added await, pass headless
        self.context = await self._get_or_create_persistent_context(headless=headless)
        page = self.context.pages[0]

        

        await page.goto("https://www.xiaohongshu.com") # Added await
        await asyncio.sleep(0.5) # Wait for page to settle

        # # Wait up to 3 seconds for the login element
        # print("检查登录状态 (最多等待3秒)...")
        # login_verified = False
        # for i in range(3):
        #     my_profile_element = await page.query_selector("span.channel:has-text('我')")
        #     if my_profile_element:
        #         print("检测到 '我' 元素，用户已登录。")
        #         login_verified = True
        #         break
        #     print(f"等待登录状态... ({i+1}/3 秒)")
        #     await asyncio.sleep(1)

        # if not login_verified and headless:
        #     # print("3秒内未检测到 '我' 元素，假定未登录或页面加载问题。无法执行搜索。")
        #     # return []
        #     raise Exception("用户未登录，无法执行搜索。")
    
        # input and search.
        await page.wait_for_selector("input#search-input", timeout=30000) # Added await
        await page.fill("input#search-input", keywords) # Added await
        await page.wait_for_selector("div.search-icon", timeout=60000) # Added await for possibly login.
        await page.click("div.search-icon", timeout=30000) # Added await
        
        try:
            await page.click("div#image.channel", timeout=10000) #图文filter, Added await
        except Exception as e_filter_click:
            print(f"无法点击 '图文' 筛选器 (可能不存在或页面结构已更改): {e_filter_click}")
            if self.logging_enabled and self.log_file_handler:
                self.log_file_handler.write(f"[{datetime.now()}] Warning: Could not click '图文' filter: {e_filter_click}\n")

        await page.wait_for_selector("section.note-item", timeout=30000) # Added await

        # 如果成功点击“图文”，则登录成功。
        self.logged_in_successfully = True 

        # Fetch note URLs
        note_elements = await page.query_selector_all("section.note-item") # Added await
        note_urls_to_visit = []
        for i, note_element in enumerate(note_elements):
            if i >= limit * 2: # Fetch more initial URLs in case some fail
                break
            url = None
            try:
                link_element = await note_element.query_selector("a[href^='/search_result/']") # Added await
                if not link_element:
                    link_element = await note_element.query_selector("a.cover.mask.ld") # Fallback, Added await
                if link_element:
                    href = await link_element.get_attribute("href") # Added await
                    if href and not href.startswith("http"):
                        url = f"https://www.xiaohongshu.com{href}"
                    elif href:
                        url = href
                if url:
                    note_urls_to_visit.append(url)
            except Exception as e_url_extract:
                print(f"提取笔记URL时出错: {e_url_extract}")
        
        # visit URLs.
        results_data = []
        for i, note_url in enumerate(note_urls_to_visit):
            if len(results_data) >= limit:
                break
            try:
                print(f"正在访问笔记 {i+1}/{len(note_urls_to_visit)}: {note_url}")
                await page.goto(note_url, wait_until="domcontentloaded", timeout=60000) # Added await
                await page.wait_for_selector("div.note-content", timeout=15000) # Added await

                # get title.
                title_element = await page.query_selector("div#detail-title.title") # Added await
                title = (await title_element.inner_text()).strip() if title_element else (await page.title()).replace(" - 小红书", "").strip() # Added await
                title_element = await page.query_selector("div#detail-title.title") # Added await
                title = (await title_element.inner_text()).strip() if title_element else (await page.title()).replace(" - 小红书", "").strip() # Added await
                
                # get content.
                content = "N/A"
                try:
                    desc_element = await page.query_selector("div#detail-desc span") # Added await
                    if desc_element:
                        content = (await desc_element.inner_text()).strip() # Added await
                except Exception as e_content:
                    print(f"提取内容时出错 {note_url}: {e_content}")

                # get images.
                
                if image_ocr:
                    # delete all images in the folder
                    image_folder = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../image'))
                    for f in os.listdir(image_folder):
                        file_path = os.path.join(image_folder, f)
                        if os.path.isfile(file_path):
                            os.remove(file_path)

                images = []

                img_elements = await page.query_selector_all("div.slide-container img.poster-image, div.swiper-slide img") # Added await
                for img_el in img_elements:
                    src = await img_el.get_attribute("src") # Added await
                    if src and src.startswith("http"):
                        # images.append(src)
                        if image_ocr:
                            image_folder = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../image'))
                            if not os.path.exists(image_folder):
                                os.makedirs(image_folder)
                            try:
                                img_name = os.path.basename(src) + '.jpg'
                                img_path = os.path.join(image_folder, img_name)
                                response = requests.get(src, timeout=10)
                                if response.status_code == 200:
                                    with open(img_path, 'wb') as f:
                                        f.write(response.content)
                            except Exception as e:
                                print(f"下载图片失败: {src}, 错误: {e}")
                            text = pytesseract.image_to_string(Image.open('image//'+img_name), lang='chi_sim+eng')
                            print(text)
                            images.append(text)
                        else:
                            images.append(src)
 
                
                # get comments.
                comments = []
                try:
                    comments_el = await page.query_selector("div.comments-el")
                    if comments_el:
                        comment_text_elements = await comments_el.query_selector_all("span.note-text span") # Select the inner span for text
                        for comment_el in comment_text_elements:
                            comment_text = await comment_el.inner_text()
                            if comment_text:
                                comments.append(comment_text.strip())
                except Exception as e_comment:
                    print(f"提取评论时出错 {note_url}: {e_comment}")
                
                # results_data.append({"url": note_url, "title": title, "content": content, "images": images})
                results_data.append({"url": note_url, "title": title, "content": content, "images": images, "comments": comments})
            except Exception as e_detail:
                print(f"处理笔记详情页 {note_url} 时出错: {e_detail}")
                if self.logging_enabled and self.log_file_handler:
                    self.log_file_handler.write(f"[{datetime.now()}] Error processing note detail {note_url}: {e_detail}\n")

        await self._save_session_state() # Added await, Save session after successful search operation
        await self.close()
        # print(results_data)
        return results_data

    async def close(self) -> None: # Added async
        print("正在准备关闭 BrowserHandler...")
        if self.context: # Check if context exists
            # _save_session_state will internally handle if context is usable
            await self._save_session_state()
        
        if self.page and not self.page.is_closed(): # Page's is_closed is fine
            try:
                await self.page.close()
                print("页面已关闭。")
            except Exception as e:
                print(f"关闭页面时出错: {e}")
            self.page = None

        if self.context: # Check if context exists
            try:
                await self.context.close()
                print("浏览器上下文已关闭。")
            except Exception as e: # Catch error if closing a problematic context
                print(f"关闭浏览器上下文时出错: {e}")
            self.context = None

        if self.playwright:
            try:
                await self.playwright.stop() # Added await
                print("Playwright已停止。")
            except Exception as e:
                print(f"停止Playwright时出错: {e}")
            self.playwright = None
        
        if self.logging_enabled and self.log_file_handler:
            try:
                print(f"关闭日志文件: {self.log_file_path}")
                self.log_file_handler.close() # sync file op
            except Exception as e:
                print(f"关闭日志文件 {self.log_file_path} 时出错: {e}")
            self.log_file_handler = None
        print("BrowserHandler 已关闭。")

# Example usage (for testing this file directly)
async def main_test(): # Added async
    USER_DATA_DIR = "C:\\Users\\myles\\AppData\\Local\\Google\\Chrome\\User Data\\Default"
    print(f"User Data Dir: {USER_DATA_DIR}")
    print(f"Storage State File: {Path(STORAGE_STATE_FILE).resolve()}")

    handler = BrowserHandler(user_data_dir=USER_DATA_DIR, enable_logging=True)

    results = await handler.search_notes(
            keywords='notion',
            limit=1,
            headless=False,
            image_ocr = True
        )
    # try:
    #     # Example: run login headful for easier debugging if needed, or keep headless=True
    #     await handler.login(headless=False) # Added await, pass headless
        
    #     if handler.logged_in_successfully and handler.page:
    #         print(f"登录成功. 当前页面 URL: {handler.page.url}")
            
    #         # search_keywords = "美食"
    #         # print(f"搜索笔记，关键词: {search_keywords}")
    #         # notes = await handler.search_notes(search_keywords, limit=2, headless=True) # Added await, pass headless
    #         # if notes:
    #         #     print(f"找到 {len(notes)} 条笔记:")
    #         #     for i, note in enumerate(notes):
    #         #         print(f"  笔记 {i+1}: {note['title']} - {note['url']}")
    #         # else:
    #         #     print("未找到笔记或搜索失败。")
    #         print("保持浏览器打开60秒以便观察，请手动关闭浏览器窗口以结束脚本。")
    #         await asyncio.sleep(3) # Changed to asyncio.sleep and added await

    #     else:
    #         print("登录失败或页面在登录尝试后不可用。")

    # except Exception as e:
    #     print(f"测试过程中发生错误: {e}")
    # finally:
    #     print("正在关闭 handler...")
    #     await handler.close() # Added await

if __name__ == '__main__':
    asyncio.run(main_test()) # Changed to asyncio.run()