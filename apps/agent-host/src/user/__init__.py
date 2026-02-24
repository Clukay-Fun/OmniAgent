"""
描述: 用户身份管理模块
主要功能:
    - 用户信息获取与缓存
    - 姓名匹配与身份绑定
    - 用户上下文管理
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


# ============================================
# region 数据模型
# ============================================
@dataclass
class UserProfile:
    """用户身份档案"""
    open_id: str
    """飞书 Open ID"""
    
    chat_id: str
    """会话 ID"""
    
    name: Optional[str] = None
    """通讯录姓名"""
    
    lawyer_name: Optional[str] = None
    """绑定的律师姓名（用于匹配主办律师字段）"""
    
    is_bound: bool = False
    """是否已完成身份绑定"""
    
    mobile: Optional[str] = None
    """手机号（可选）"""
    
    email: Optional[str] = None
    """邮箱（可选）"""
    
    cached_at: Optional[datetime] = None
    """缓存时间"""
    
    def __post_init__(self):
        """初始化后处理"""
        if self.cached_at is None:
            self.cached_at = datetime.now()
        
        # 如果已有姓名但未绑定律师名，默认使用姓名
        if self.name and not self.lawyer_name:
            self.lawyer_name = self.name
            self.is_bound = False  # 需要验证匹配
# endregion
