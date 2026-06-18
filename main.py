"""
阿里云 OSS 管理工具 - 启动入口

首次启动: 弹出配置对话框 → 输入凭证 → 验证保存 → 进入主界面
后续启动: 自动加载 SQLite 中的配置 → 连接验证 → 进入主界面

使用方法:
    .\venv\Scripts\python.exe main.py
"""

import sys
from PyQt5.QtWidgets import QApplication, QMessageBox

from config_manager import load_config, delete_config, save_config
from config_dialog import ConfigDialog
from oss_client import OssClient
from oss_gui import OssManagerMain


def create_client(config: dict) -> OssClient:
    """根据配置字典创建 OssClient"""
    return OssClient(
        access_key_id=config["access_key_id"],
        access_key_secret=config["access_key_secret"],
        endpoint=config["endpoint"],
        bucket=config["bucket"],
        region=config.get("region", ""),
    )


def verify_connection(client: OssClient) -> tuple:
    """验证 OSS 连接，返回 (success: bool, message: str)"""
    try:
        info = client.get_bucket_info()
        return True, f"连接成功 — Bucket: {info['name']} ({info['region']})"
    except Exception as e:
        return False, f"连接失败: {str(e)}"


def show_config_dialog(parent=None) -> dict:
    """显示配置对话框，返回配置字典；用户取消则返回 None"""
    dlg = ConfigDialog(parent)
    if dlg.exec_() != ConfigDialog.Accepted:
        return None
    return load_config()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # ── 加载配置 ──
    config = load_config()

    if config is not None:
        # 已有保存的配置，尝试验证连接
        client = create_client(config)
        ok, msg = verify_connection(client)
        if not ok:
            QMessageBox.warning(
                None, "连接失败",
                f"已保存的配置无法连接 OSS:\n\n{msg}\n\n请重新配置。"
            )
            delete_config()
            config = None

    # ── 无有效配置，弹出配置对话框 ──
    if config is None:
        while True:
            config = show_config_dialog()
            if config is None:
                sys.exit(0)  # 用户取消

            client = create_client(config)
            ok, msg = verify_connection(client)
            if ok:
                break  # 配置有效，进入主界面
            else:
                QMessageBox.critical(
                    None, "验证失败",
                    f"{msg}\n\n请检查后重新输入。"
                )
                # 删除失败配置
                delete_config()
                config = None
    else:
        client = create_client(config)

    # ── 启动主窗口 ──
    window = OssManagerMain(client)

    # 菜单：重新配置
    window.reconfigure_requested.connect(lambda: _do_reconfigure(window))

    window.show()
    sys.exit(app.exec_())


def _do_reconfigure(window: OssManagerMain):
    """重新配置：弹出配置对话框，更新连接"""
    reply = QMessageBox.question(
        window, "重新配置",
        "确定要修改 OSS 连接配置吗？\n当前配置将被替换。",
        QMessageBox.Yes | QMessageBox.No,
    )
    if reply != QMessageBox.Yes:
        return

    config = show_config_dialog(window)
    if config is None:
        return

    client = create_client(config)
    ok, msg = verify_connection(client)
    if not ok:
        QMessageBox.critical(window, "验证失败", msg)
        return

    # 替换主窗口的 client 并刷新
    window.client = client
    window.setWindowTitle(f"OSS 管理工具 - {client.bucket}")
    window._refresh()


if __name__ == "__main__":
    main()
