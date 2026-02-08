"""
描述: 用户管理器
主要功能:
    - 统一用户身份管理入口
    - 自动匹配 + 手动绑定
    - 通讯录查询集成
"""

from __future__ import annotations

import logging
from typing import Optional

from src.user import UserProfile
from src.user.cache import UserCache
from src.user.matcher import UserMatcher
from src.utils.feishu_api import get_token_manager
from src.config import Settings
import httpx


logger = logging.getLogger(__name__)


# ============================================
# region 用户管理器
# ============================================
class UserManager:
    """
    用户管理器
    
    功能:
        - 获取用户信息（通讯录）
        - 自动身份匹配
        - 手动绑定管理
        - 缓存管理
    """
    
    def __init__(
        self,
        settings: Settings,
        matcher: UserMatcher,
        cache: Optional[UserCache] = None,
    ):
        """
        初始化用户管理器
        
        参数:
            settings: 全局配置
            matcher: 身份匹配器
            cache: 用户缓存（可选，默认创建新实例）
        """
        self._settings = settings
        self._matcher = matcher
        self._cache = cache or UserCache()
    
    async def get_or_create_profile(
        self,
        open_id: str,
        chat_id: str,
        auto_match: bool = True,
    ) -> UserProfile:
        """
        获取或创建用户档案
        
        参数:
            open_id: 用户 Open ID
            chat_id: 会话 ID
            auto_match: 是否自动匹配身份
            
        返回:
            UserProfile
        """
        # 1. 尝试从缓存获取
        cached = self._cache.get(open_id)
        if cached is not None:
            if not cached.name:
                user_info = await self._fetch_user_info(open_id)
                if user_info.get("name"):
                    cached.name = user_info.get("name")
                if user_info.get("mobile"):
                    cached.mobile = user_info.get("mobile")
                if user_info.get("email"):
                    cached.email = user_info.get("email")
                if cached.name and not cached.lawyer_name:
                    cached.lawyer_name = cached.name
                self._cache.set(cached)
            logger.debug(f"User profile loaded from cache: {open_id}")
            return cached
        
        # 2. 从通讯录获取用户信息
        user_info = await self._fetch_user_info(open_id)
        
        # 3. 创建档案
        profile = UserProfile(
            open_id=open_id,
            chat_id=chat_id,
            name=user_info.get("name"),
            mobile=user_info.get("mobile"),
            email=user_info.get("email"),
        )
        
        # 4. 自动匹配
        if auto_match and profile.name:
            await self._auto_match(profile)
        
        # 5. 缓存
        self._cache.set(profile)
        
        logger.info(
            f"User profile created: open_id={open_id}, name={profile.name}, "
            f"is_bound={profile.is_bound}"
        )
        
        return profile
    
    async def bind_lawyer_name(self, open_id: str, lawyer_name: str) -> tuple[bool, str]:
        """
        手动绑定律师姓名
        
        参数:
            open_id: 用户 Open ID
            lawyer_name: 律师姓名
            
        返回:
            (是否成功, 消息)
        """
        # 1. 验证姓名是否有效
        is_valid = await self._matcher.verify_binding(lawyer_name)
        if not is_valid:
            return False, f"未找到律师'{lawyer_name}'的案件记录，请确认姓名是否正确。"
        
        # 2. 更新档案
        profile = self._cache.get(open_id)
        if profile is None:
            return False, "用户档案不存在，请先发送消息初始化。"
        
        profile.lawyer_name = lawyer_name
        profile.is_bound = True
        self._cache.set(profile)
        
        logger.info(f"User {open_id} manually bound to lawyer: {lawyer_name}")
        return True, f"绑定成功！已将您的身份关联到律师'{lawyer_name}'。"
    
    async def _fetch_user_info(self, open_id: str) -> dict:
        """
        从飞书通讯录获取用户信息
        
        参数:
            open_id: 用户 Open ID
            
        返回:
            用户信息字典 {name, mobile, email}
        """
        try:
            logger.info(f"Fetching user info for open_id: {open_id}")
            token = await get_token_manager(self._settings).get_token()
            logger.info(f"Got token, calling contact API...")
            url = f"{self._settings.feishu.api_base}/contact/v3/users/{open_id}"
            params = {"user_id_type": "open_id"}
            
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                response = await client.get(
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
                data = response.json()
            
            logger.info(f"Contact API response code: {data.get('code')}")
            
            if data.get("code") != 0:
                logger.warning(f"Failed to fetch user info: {data.get('msg')}")
                return {}
            
            user = data.get("data", {}).get("user", {})
            logger.info(f"User info fetched: name={user.get('name')}")
            return {
                "name": user.get("name"),
                "mobile": user.get("mobile"),
                "email": user.get("email"),
            }
            
        except Exception as e:
            logger.error(f"Error fetching user info for {open_id}: {e}", exc_info=True)
            return {}
    
    async def _auto_match(self, profile: UserProfile) -> None:
        """
        自动匹配用户身份
        
        参数:
            profile: 用户档案（会被修改）
        """
        if not profile.name:
            return
        
        is_matched, confidence, records = await self._matcher.match_by_name(profile.name)
        
        if is_matched:
            profile.lawyer_name = profile.name
            profile.is_bound = True
            logger.info(
                f"Auto-matched user {profile.open_id} to lawyer '{profile.name}' "
                f"(confidence={confidence:.2f})"
            )
        else:
            logger.info(
                f"Auto-match failed for user {profile.open_id} (name='{profile.name}', "
                f"confidence={confidence:.2f})"
            )
# endregion
