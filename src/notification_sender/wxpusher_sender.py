# -*- coding: utf-8 -*-
"""
WXPusher 发送提醒服务

职责：
1. 通过 WXPusher API 发送消息
"""
import logging
import re
from typing import Optional
from datetime import datetime
import requests

from src.config import Config


logger = logging.getLogger(__name__)


def _markdown_to_html(text: str) -> str:
    """
    将 Markdown 文本转换为 HTML

    支持：标题、加粗、斜体、链接、列表、表格、换行
    """
    lines = text.split('\n')
    in_table = False
    in_list = False
    result_lines = []

    for line in lines:
        stripped = line.strip()

        if stripped.startswith('|') and '|' in stripped[1:]:
            if not in_table:
                in_table = True
                result_lines.append('<table>')
            cols = [c.strip() for c in stripped.strip('|').split('|')]
            if all(re.match(r'^-+$', c) for c in cols):
                continue
            result_lines.append('<tr>' + ''.join(f'<td>{c}</td>' for c in cols) + '</tr>')
            continue
        else:
            if in_table:
                in_table = False
                result_lines.append('</table>')

        if stripped.startswith('### '):
            result_lines.append(f'<h3>{stripped[4:]}</h3>')
        elif stripped.startswith('## '):
            result_lines.append(f'<h2>{stripped[3:]}</h2>')
        elif stripped.startswith('# '):
            result_lines.append(f'<h1>{stripped[2:]}</h1>')
        elif stripped.startswith('- ') or stripped.startswith('* '):
            if not in_list:
                in_list = True
                result_lines.append('<ul>')
            result_lines.append(f'<li>{stripped[2:]}</li>')
        elif stripped == '' or stripped == '---':
            result_lines.append('<br>')
        else:
            bold_line = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', stripped)
            italic_line = re.sub(r'\*(.+?)\*', r'<em>\1</em>', bold_line)
            link_line = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', italic_line)
            result_lines.append(f'<p>{link_line}</p>')

    if in_table:
        result_lines.append('</table>')
    if in_list:
        result_lines.append('</ul>')

    html = '\n'.join(result_lines)
    html = re.sub(r'<br><br>', '<br>', html)
    html = re.sub(r'<p><br>', '<p>', html)
    html = re.sub(r'<br></p>', '</p>', html)
    return html


class WxpusherSender:

    def __init__(self, config: Config):
        """
        初始化 WXPusher 配置

        Args:
            config: 配置对象
        """
        self._wxpusher_app_token = getattr(config, 'wxpusher_app_token', None)
        self._wxpusher_uid = getattr(config, 'wxpusher_uid', None)
        self._wxpusher_ilink_uin = getattr(config, 'wxpusher_ilink_uin', None)

    def send_to_wxpusher(self, content: str, title: Optional[str] = None) -> bool:
        """
        推送消息到 WXPusher

        推送策略（优先级递减）：
        1. iLink 通道：如果配置了 WXPUSHER_ILINK_UIN，优先使用 iLink 推送至微信
        2. UID 通道：降级方案，通过 WXPusher App 推送

        iLink API 格式：
        POST https://wxpusher.zjiecode.com/api/send/message/
        {
            "appToken": "用户Token",
            "iLinkUins": ["iLink用户UIN"],
            "content": "消息内容",
            "contentType": 2,
            "summary": "标题（微信消息列表显示）"
        }

        UID API 格式：
        POST https://wxpusher.zjiecode.com/api/send/message/
        {
            "appToken": "用户Token",
            "uids": ["用户UID"],
            "content": "消息内容（HTML）",
            "contentType": 2,
            "title": "标题"
        }

        Args:
            content: 消息内容（Markdown 格式，内部会转 HTML）
            title: 消息标题（可选）

        Returns:
            是否发送成功
        """
        if not self._wxpusher_app_token:
            logger.warning("WXPusher APP Token 未配置，跳过推送")
            return False

        api_url = "https://wxpusher.zjiecode.com/api/send/message/"

        if title is None:
            date_str = datetime.now().strftime('%Y-%m-%d')
            title = f"股票分析报告 - {date_str}"

        if self._wxpusher_ilink_uin:
            ilink_uins = [u.strip() for u in self._wxpusher_ilink_uin.split(',') if u.strip()]
            if ilink_uins:
                logger.info(f"优先使用 iLink 通道推送，目标 UIN: {ilink_uins}")
                html_content = _markdown_to_html(content)
                ilink_payload = {
                    "appToken": self._wxpusher_app_token,
                    "iLinkUins": ilink_uins,
                    "content": html_content,
                    "contentType": 2,
                    "summary": title,
                }
                if self._send_via_ilink(api_url, ilink_payload):
                    return True
                logger.warning("iLink 通道推送失败，尝试降级到 UID 通道")

        if not self._wxpusher_uid:
            logger.warning("WXPusher UID 未配置，且 iLink 通道不可用，跳过推送")
            return False

        uids = [uid.strip() for uid in self._wxpusher_uid.split(',') if uid.strip()]
        if not uids:
            logger.warning("WXPusher UID 格式无效，跳过推送")
            return False

        logger.info(f"使用 UID 通道降级推送，目标 UID: {uids}")
        html_content = _markdown_to_html(content)
        uid_payload = {
            "appToken": self._wxpusher_app_token,
            "uids": uids,
            "content": html_content,
            "contentType": 2,
            "title": title,
        }
        return self._send_via_uid(api_url, uid_payload)

    def _send_via_ilink(self, api_url: str, payload: dict) -> bool:
        """通过 iLink 通道发送请求"""
        try:
            response = requests.post(api_url, json=payload, timeout=10)
            result = response.json()
            if response.status_code == 200 and result.get('code') == 1000:
                logger.info("WXPusher iLink 推送成功")
                return True
            error_msg = result.get('msg', '未知错误')
            logger.error(f"WXPusher iLink 返回错误: {error_msg}")
            return False
        except requests.exceptions.Timeout:
            logger.error("WXPusher iLink 请求超时")
            return False
        except Exception as e:
            logger.error(f"发送 WXPusher iLink 消息失败: {e}")
            return False

    def _send_via_uid(self, api_url: str, payload: dict) -> bool:
        """通过 UID 通道发送请求"""
        try:
            response = requests.post(api_url, json=payload, timeout=10)
            result = response.json()
            if response.status_code == 200 and result.get('code') == 1000:
                logger.info("WXPusher UID 推送成功")
                return True
            error_msg = result.get('msg', '未知错误')
            logger.error(f"WXPusher UID 返回错误: {error_msg}")
            return False
        except requests.exceptions.Timeout:
            logger.error("WXPusher UID 请求超时")
            return False
        except Exception as e:
            logger.error(f"发送 WXPusher UID 消息失败: {e}")
            return False
