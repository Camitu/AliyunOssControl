"""
OSS 初始化 / 登录配置对话框

首次启动或重置配置后，弹出此对话框让用户手动输入：
- AccessKey ID
- AccessKey Secret
- Bucket 名称
- Endpoint 域名

点击"验证并保存"后，尝试连接 OSS 获取 Bucket 信息：
- 成功 → 保存到本地 SQLite，关闭对话框
- 失败 → 显示错误，留在当前界面
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QPushButton, QLabel, QMessageBox, QGroupBox,
    QTextEdit, QCheckBox, QProgressBar,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

from oss_client import OssClient
from config_manager import save_config, delete_config, has_config


def _extract_region(endpoint: str) -> str:
    """从 endpoint 提取 region"""
    parts = endpoint.replace(".aliyuncs.com", "").replace(".internal", "")
    return parts.replace("oss-", "")


class ConfigDialog(QDialog):
    """OSS 连接配置对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("阿里云 OSS - 连接配置")
        self.setFixedSize(520, 480)
        self._setup_ui()
        self._restore_secret_visibility()

        # 如果已有保存的配置，自动填入
        if has_config():
            from config_manager import load_config
            cfg = load_config()
            if cfg:
                self.edit_key_id.setText(cfg.get("access_key_id", ""))
                self.edit_key_secret.setText(cfg.get("access_key_secret", ""))
                self.edit_bucket.setText(cfg.get("bucket", ""))
                self.edit_endpoint.setText(cfg.get("endpoint", ""))

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(12)

        # ---- 标题 ----
        title = QLabel("请输入阿里云 OSS 连接信息")
        title.setAlignment(Qt.AlignCenter)
        font = QFont()
        font.setPointSize(13)
        font.setBold(True)
        title.setFont(font)
        root.addWidget(title)

        # ---- 输入表单 ----
        group = QGroupBox("连接凭证")
        form = QFormLayout(group)
        form.setSpacing(8)

        self.edit_key_id = QLineEdit()
        self.edit_key_id.setPlaceholderText("例如 LTAI5t...")
        form.addRow("AccessKey ID:", self.edit_key_id)

        # Secret 输入框 + 显示/隐藏切换
        secret_layout = QHBoxLayout()
        self.edit_key_secret = QLineEdit()
        self.edit_key_secret.setEchoMode(QLineEdit.Password)
        self.edit_key_secret.setPlaceholderText("例如 zyk2B...")
        secret_layout.addWidget(self.edit_key_secret)

        self.chk_show_secret = QCheckBox("显示")
        self.chk_show_secret.toggled.connect(self._restore_secret_visibility)
        secret_layout.addWidget(self.chk_show_secret)
        form.addRow("AccessKey Secret:", secret_layout)

        self.edit_bucket = QLineEdit()
        self.edit_bucket.setPlaceholderText("例如 restore")
        form.addRow("Bucket 名称:", self.edit_bucket)

        self.edit_endpoint = QLineEdit()
        self.edit_endpoint.setPlaceholderText("例如 oss-cn-hangzhou.aliyuncs.com")
        form.addRow("Endpoint:", self.edit_endpoint)

        root.addWidget(group)

        # ---- 进度条 ----
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)

        # ---- 结果展示区 ----
        self.info_area = QTextEdit()
        self.info_area.setReadOnly(True)
        self.info_area.setMaximumHeight(100)
        self.info_area.setVisible(False)
        self.info_area.setStyleSheet("background-color: #f5f5f5; border: 1px solid #ccc;")
        root.addWidget(self.info_area)

        # ---- 按钮区 ----
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_test = QPushButton("验证并保存")
        self.btn_test.setMinimumWidth(130)
        self.btn_test.setDefault(True)
        self.btn_test.clicked.connect(self._on_test_and_save)
        btn_layout.addWidget(self.btn_test)

        self.btn_cancel = QPushButton("退出程序")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        btn_layout.addStretch()
        root.addLayout(btn_layout)

    def _restore_secret_visibility(self):
        """根据复选框状态切换 Secret 明文/密文"""
        if self.chk_show_secret.isChecked():
            self.edit_key_secret.setEchoMode(QLineEdit.Normal)
        else:
            self.edit_key_secret.setEchoMode(QLineEdit.Password)

    def _on_test_and_save(self):
        """验证连接 → 成功则保存并关闭"""
        key_id = self.edit_key_id.text().strip()
        key_secret = self.edit_key_secret.text().strip()
        bucket = self.edit_bucket.text().strip()
        endpoint = self.edit_endpoint.text().strip()

        # 基本校验
        if not all([key_id, key_secret, bucket, endpoint]):
            QMessageBox.warning(self, "输入不完整", "请填写所有字段后再试。")
            return

        region = _extract_region(endpoint)

        # 禁用按钮，显示进度
        self.btn_test.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)  # 不确定进度
        self.info_area.setVisible(False)

        # 执行验证（在主线程中，因为 OSS API 是同步的，QDialog 不会卡死太久）
        try:
            client = OssClient(key_id, key_secret, endpoint, bucket, region)
            info = client.get_bucket_info()
            stat = client.get_bucket_stat()

            # 构造结果文本
            lines = [
                "✅ 连接成功！Bucket 信息如下:",
                f"  名称: {info['name']}",
                f"  地域: {info['region']}",
                f"  存储类型: {info['storage_class']}",
                f"  访问权限: {info['acl']}",
                f"  创建时间: {info['creation_date']}",
                f"  对象数量: {stat['object_count']}",
                f"  存储用量: {stat['storage']}",
            ]
            self.info_area.setText("\n".join(lines))
            self.info_area.setVisible(True)

            # 保存配置到 SQLite
            save_config(key_id, key_secret, bucket, endpoint, region)

            # 延迟一小会儿让用户看到结果，然后关闭
            QMessageBox.information(self, "配置已保存", "连接验证成功，配置已保存到本地。\n下次启动将自动加载。")
            self.accept()

        except Exception as e:
            self.info_area.setText(f"❌ 连接失败:\n{str(e)}")
            self.info_area.setVisible(True)
            QMessageBox.critical(self, "验证失败", f"无法连接到 OSS，请检查配置:\n\n{str(e)}")

        finally:
            self.btn_test.setEnabled(True)
            self.btn_cancel.setEnabled(True)
            self.progress.setVisible(False)

    def get_config_dict(self) -> dict:
        """返回当前输入的配置字典（仅在 accept 后调用有意义）"""
        return {
            "access_key_id": self.edit_key_id.text().strip(),
            "access_key_secret": self.edit_key_secret.text().strip(),
            "bucket": self.edit_bucket.text().strip(),
            "endpoint": self.edit_endpoint.text().strip(),
            "region": _extract_region(self.edit_endpoint.text().strip()),
        }
