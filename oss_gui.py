"""
阿里云 OSS 管理工具 - PyQt5 可视化客户端
"""
import os
import sys
import threading

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem, QTableWidget, QTableWidgetItem,
    QPushButton, QLineEdit, QLabel, QSplitter, QHeaderView,
    QProgressBar, QStatusBar, QMessageBox, QMenu, QMenuBar, QAction,
    QInputDialog, QFileDialog, QDialog, QDialogButtonBox,
    QFormLayout, QGroupBox, QAbstractItemView, QStyle,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QThreadPool, QRunnable
from PyQt5.QtGui import QIcon, QFont

from oss_client import OssClient, fmt_size, fmt_time


# ── 工作线程：后台执行 OSS 操作 ──────────────────────────────────────────

class WorkerSignals:
    """工作线程信号容器"""
    def __init__(self):
        self.finished = pyqtSignal(object)  # 结果
        self.error = pyqtSignal(str)         # 错误消息
        self.progress = pyqtSignal(int, int) # (已完成, 总量)


class OssWorker(QThread):
    """后台执行 OSS 操作的线程"""
    finished = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(int, int)

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self._progress_fn = kwargs.pop("progress_fn", None)

    def run(self):
        try:
            if self._progress_fn:
                self.kwargs["progress_fn"] = self._make_progress
            result = self.func(*self.args, **self.kwargs)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))

    def _make_progress(self, sent, total):
        self.progress.emit(sent, total)


# ── 预签名 URL 对话框 ──────────────────────────────────────────────────

class PresignDialog(QDialog):
    """生成预签名 URL 对话框"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("生成预签名 URL")
        self.setMinimumWidth(600)
        self._setup_ui()

    def _setup_ui(self):
        layout = QFormLayout(self)

        self.key_edit = QLineEdit()
        self.key_edit.setReadOnly(True)
        layout.addRow("对象名:", self.key_edit)

        self.method_combo = QLineEdit("GET")
        layout.addRow("HTTP 方法:", self.method_combo)

        self.expires_edit = QLineEdit("3600")
        layout.addRow("有效期(秒):", self.expires_edit)

        self.url_edit = QLineEdit()
        self.url_edit.setReadOnly(True)
        layout.addRow("签名 URL:", self.url_edit)

        btn_layout = QHBoxLayout()
        self.generate_btn = QPushButton("生成")
        self.copy_btn = QPushButton("复制 URL")
        self.close_btn = QPushButton("关闭")
        btn_layout.addWidget(self.generate_btn)
        btn_layout.addWidget(self.copy_btn)
        btn_layout.addWidget(self.close_btn)
        layout.addRow(btn_layout)

        self.close_btn.clicked.connect(self.close)
        self.copy_btn.clicked.connect(self._copy_url)

    def set_key(self, key: str):
        self.key_edit.setText(key)

    def _copy_url(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self.url_edit.text())
        QMessageBox.information(self, "已复制", "URL 已复制到剪贴板")


# ── 主窗口 ───────────────────────────────────────────────────────────

class OssManagerMain(QMainWindow):
    """OSS 管理工具主窗口"""

    # 请求重新配置信号 → main.py 中处理
    reconfigure_requested = pyqtSignal()

    def __init__(self, client: OssClient):
        super().__init__()
        self.client = client
        self._current_prefix = ""  # 当前浏览的目录前缀
        self._nav_history = [""]   # 导航历史
        self._nav_index = 0

        self.setWindowTitle(f"OSS 管理工具 - {client.bucket}")
        self.resize(1200, 750)
        self._setup_ui()
        self._refresh()

    # ── UI 构建 ──────────────────────────────────────────────────

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setSpacing(6)

        # ---------- 菜单栏 ----------
        menu_bar = self.menuBar()
        menu_config = menu_bar.addMenu("配置(&C)")
        action_reconfig = QAction("重新配置登录凭证", self)
        action_reconfig.triggered.connect(self.reconfigure_requested.emit)
        menu_config.addAction(action_reconfig)
        menu_config.addSeparator()
        action_about = QAction("关于", self)
        action_about.triggered.connect(
            lambda: QMessageBox.about(self, "关于", "阿里云 OSS 管理工具 v1.0\n基于 alibabacloud_oss_v2 SDK")
        )
        menu_config.addAction(action_about)

        # ---------- 顶部工具栏 ----------
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel(f"Bucket: <b>{self.client.bucket}</b>"))
        toolbar.addWidget(QLabel(f"|  Endpoint: {self.client.endpoint}"))

        toolbar.addStretch()

        self.path_edit = QLineEdit("")
        self.path_edit.setPlaceholderText("输入路径后回车跳转...")
        self.path_edit.returnPressed.connect(self._on_path_enter)
        toolbar.addWidget(self.path_edit, 1)

        self.btn_info = QPushButton("Bucket 信息")
        self.btn_info.clicked.connect(self._show_bucket_info)
        toolbar.addWidget(self.btn_info)

        self.btn_stat = QPushButton("存储统计")
        self.btn_stat.clicked.connect(self._show_bucket_stat)
        toolbar.addWidget(self.btn_stat)

        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.clicked.connect(self._refresh)
        toolbar.addWidget(self.btn_refresh)

        root_layout.addLayout(toolbar)

        # ---------- 面包屑导航 ----------
        nav_layout = QHBoxLayout()

        self.btn_back = QPushButton("< 后退")
        self.btn_back.clicked.connect(self._nav_back)
        nav_layout.addWidget(self.btn_back)

        self.btn_forward = QPushButton("前进 >")
        self.btn_forward.clicked.connect(self._nav_forward)
        nav_layout.addWidget(self.btn_forward)

        self.btn_root = QPushButton("根目录")
        self.btn_root.clicked.connect(self._goto_root)
        nav_layout.addWidget(self.btn_root)

        self.lbl_breadcrumb = QLabel("/")
        self.lbl_breadcrumb.setStyleSheet("color: #555; font-size: 13px;")
        nav_layout.addWidget(self.lbl_breadcrumb, 1)

        root_layout.addLayout(nav_layout)

        # ---------- 分割主区域 ----------
        splitter = QSplitter(Qt.Horizontal)

        # 左侧目录树
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["目录"])
        self.tree.setMinimumWidth(220)
        self.tree.setMaximumWidth(380)
        self.tree.itemExpanded.connect(self._on_tree_expand)
        self.tree.itemClicked.connect(self._on_tree_clicked)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_context_menu)
        splitter.addWidget(self.tree)

        # 右侧文件表格
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["名称", "大小", "修改时间", "类型", "ETag"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_context_menu)
        self.table.doubleClicked.connect(self._on_table_double_click)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        splitter.addWidget(self.table)

        splitter.setSizes([260, 940])
        root_layout.addWidget(splitter, 1)

        # ---------- 底部操作栏 ----------
        action_bar = QHBoxLayout()

        self.btn_upload = QPushButton("上传文件")
        self.btn_upload.clicked.connect(self._upload_file)
        action_bar.addWidget(self.btn_upload)

        self.btn_download = QPushButton("下载选中")
        self.btn_download.clicked.connect(self._download_selected)
        action_bar.addWidget(self.btn_download)

        self.btn_delete = QPushButton("删除选中")
        self.btn_delete.clicked.connect(self._delete_selected)
        action_bar.addWidget(self.btn_delete)

        self.btn_mkdir = QPushButton("创建目录")
        self.btn_mkdir.clicked.connect(self._create_directory)
        action_bar.addWidget(self.btn_mkdir)

        self.btn_rmdir = QPushButton("删除目录")
        self.btn_rmdir.clicked.connect(self._delete_directory)
        action_bar.addWidget(self.btn_rmdir)

        self.btn_rename = QPushButton("重命名")
        self.btn_rename.clicked.connect(self._rename_selected)
        action_bar.addWidget(self.btn_rename)

        self.btn_presign = QPushButton("签名 URL")
        self.btn_presign.clicked.connect(self._presign_selected)
        action_bar.addWidget(self.btn_presign)

        action_bar.addStretch()

        root_layout.addLayout(action_bar)

        # ---------- 进度条 ----------
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        root_layout.addWidget(self.progress_bar)

        # ---------- 状态栏 ----------
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("就绪")

    # ── 数据刷新 ──────────────────────────────────────────────────

    def _refresh(self):
        """刷新当前目录的文件列表"""
        self.status_bar.showMessage("加载中...")
        self.btn_refresh.setEnabled(False)

        self.worker = OssWorker(self.client.list_directory, self._current_prefix)
        self.worker.finished.connect(self._on_list_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_list_finished(self, data: dict):
        self.btn_refresh.setEnabled(True)
        files = data.get("files", [])
        dirs = data.get("dirs", [])

        # 更新面包屑
        self.lbl_breadcrumb.setText(f" / {self._current_prefix}" if self._current_prefix else " /")
        self.path_edit.setText(self._current_prefix)

        # 更新表格
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)

        # 先显示目录
        row = 0
        for d in sorted(dirs, key=lambda x: x["prefix"]):
            self.table.insertRow(row)
            name = d["prefix"]
            # 去掉前缀，只显示相对名
            if name.startswith(self._current_prefix):
                display_name = name[len(self._current_prefix):]
            else:
                display_name = name

            item = QTableWidgetItem(display_name)
            item.setData(Qt.UserRole, name)  # 完整 key
            item.setData(Qt.UserRole + 1, "dir")
            self.table.setItem(row, 0, item)
            self.table.setItem(row, 1, QTableWidgetItem("—"))
            self.table.setItem(row, 2, QTableWidgetItem("—"))
            self.table.setItem(row, 3, QTableWidgetItem("[目录]"))
            self.table.setItem(row, 4, QTableWidgetItem("—"))
            row += 1

        # 再显示文件
        for f in sorted(files, key=lambda x: x["key"]):
            self.table.insertRow(row)
            name = f["key"]
            if name.startswith(self._current_prefix):
                display_name = name[len(self._current_prefix):]
            else:
                display_name = name

            # 跳过目录标记文件（以 / 结尾的零字节文件）
            if display_name.endswith("/"):
                continue

            item = QTableWidgetItem(display_name)
            item.setData(Qt.UserRole, name)
            item.setData(Qt.UserRole + 1, "file")
            self.table.setItem(row, 0, item)
            self.table.setItem(row, 1, QTableWidgetItem(f["size_display"]))
            self.table.setItem(row, 2, QTableWidgetItem(f["last_modified"]))
            # 简单判断类型
            ext = os.path.splitext(display_name)[1].lower()
            ftype = ext if ext else "—"
            self.table.setItem(row, 3, QTableWidgetItem(ftype))
            self.table.setItem(row, 4, QTableWidgetItem(f.get("etag", "—")))
            row += 1

        self.table.setSortingEnabled(True)
        self.status_bar.showMessage(f"共 {len(dirs)} 个目录, {len(files)} 个文件")

        # 刷新目录树
        self._refresh_tree(dirs)

    def _on_error(self, msg: str):
        self.btn_refresh.setEnabled(True)
        self.status_bar.showMessage("操作失败")
        QMessageBox.critical(self, "错误", f"操作失败:\n{msg}")

    # ── 目录树 ────────────────────────────────────────────────────

    def _refresh_tree(self, dirs: list):
        """刷新左侧目录树"""
        # 找到或创建对应的树节点
        self.tree.clear()
        root = QTreeWidgetItem(self.tree, ["/"])
        root.setData(0, Qt.UserRole, "")
        root.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)

        # 添加一个占位子节点以便展开
        placeholder = QTreeWidgetItem(root, ["加载中..."])
        placeholder.setData(0, Qt.UserRole, "__placeholder__")

    def _on_tree_expand(self, item: QTreeWidgetItem):
        """目录树节点展开时加载子目录"""
        prefix = item.data(0, Qt.UserRole)
        # 移除占位
        while item.childCount() > 0 and item.child(0).data(0, Qt.UserRole) == "__placeholder__":
            item.removeChild(item.child(0))

        if item.childCount() > 0:
            return  # 已加载过

        try:
            result = self.client.list_directory(prefix)
            for d in sorted(result["dirs"], key=lambda x: x["prefix"]):
                child = QTreeWidgetItem(item)
                # 显示名
                name = d["prefix"]
                if prefix and name.startswith(prefix):
                    display = name[len(prefix):]
                else:
                    display = name
                child.setText(0, display)
                child.setData(0, Qt.UserRole, name)
                child.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
                # 加占位
                ph = QTreeWidgetItem(child, ["加载中..."])
                ph.setData(0, Qt.UserRole, "__placeholder__")
        except Exception:
            pass

    def _on_tree_clicked(self, item: QTreeWidgetItem, col: int):
        """点击目录树节点导航"""
        prefix = item.data(0, Qt.UserRole)
        if prefix != self._current_prefix:
            self._navigate_to(prefix)

    def _tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        prefix = item.data(0, Qt.UserRole)
        menu = QMenu(self)
        action_del = menu.addAction("删除此目录")
        action = menu.exec_(self.tree.viewport().mapToGlobal(pos))
        if action == action_del:
            self._do_delete_directory(prefix)

    # ── 表格交互 ──────────────────────────────────────────────────

    def _on_table_double_click(self, index):
        """双击表格行：目录进入，文件下载"""
        row = index.row()
        item = self.table.item(row, 0)
        key = item.data(Qt.UserRole)
        ftype = item.data(Qt.UserRole + 1)

        if ftype == "dir":
            self._navigate_to(key)
        else:
            self._download_file(key)

    def _table_context_menu(self, pos):
        """表格右键菜单"""
        rows = set(idx.row() for idx in self.table.selectedIndexes())
        if not rows:
            return

        menu = QMenu(self)
        # 收集选中项
        selected = []
        for r in rows:
            item = self.table.item(r, 0)
            if item:
                selected.append({
                    "key": item.data(Qt.UserRole),
                    "type": item.data(Qt.UserRole + 1),
                    "name": item.text(),
                })

        action_dl = menu.addAction("下载")
        action_del = menu.addAction("删除")
        action_rename = menu.addAction("重命名")
        menu.addSeparator()
        action_url = menu.addAction("生成签名 URL")

        action = menu.exec_(self.table.viewport().mapToGlobal(pos))

        if not selected:
            return
        s = selected[0]

        if action == action_dl:
            if s["type"] == "file":
                self._download_file(s["key"])
        elif action == action_del:
            self._do_delete_selected(selected)
        elif action == action_rename:
            self._do_rename(s)
        elif action == action_url:
            self._show_presign_dialog(s["key"])

    # ── 导航 ──────────────────────────────────────────────────────

    def _navigate_to(self, prefix: str):
        """跳转到指定前缀"""
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        # 更新历史
        if self._nav_index < len(self._nav_history) - 1:
            self._nav_history = self._nav_history[:self._nav_index + 1]
        self._nav_history.append(prefix)
        self._nav_index = len(self._nav_history) - 1
        self._current_prefix = prefix
        self._refresh()

    def _goto_root(self):
        self._navigate_to("")

    def _nav_back(self):
        if self._nav_index > 0:
            self._nav_index -= 1
            self._current_prefix = self._nav_history[self._nav_index]
            self._refresh()

    def _nav_forward(self):
        if self._nav_index < len(self._nav_history) - 1:
            self._nav_index += 1
            self._current_prefix = self._nav_history[self._nav_index]
            self._refresh()

    def _on_path_enter(self):
        path = self.path_edit.text().strip()
        self._navigate_to(path)

    # ── 操作 ──────────────────────────────────────────────────────

    def _upload_file(self):
        """上传文件"""
        local_path, _ = QFileDialog.getOpenFileName(self, "选择要上传的文件")
        if not local_path:
            return
        filename = os.path.basename(local_path)
        oss_key = self._current_prefix + filename

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_bar.showMessage("上传中...")
        self._set_buttons_enabled(False)

        self.worker = OssWorker(
            self.client.upload_file,
            local_path, oss_key,
        )
        self.worker.finished.connect(lambda r: self._on_upload_done(r, oss_key))
        self.worker.error.connect(self._on_error)
        self.worker.progress.connect(self._on_progress)
        self.worker.start()

    def _on_upload_done(self, result: dict, oss_key: str):
        self.progress_bar.setVisible(False)
        self._set_buttons_enabled(True)
        self.status_bar.showMessage(f"上传完成: {oss_key}")
        QMessageBox.information(self, "上传完成", f"文件已上传:\n{oss_key}")
        self._refresh()

    def _download_file(self, oss_key: str):
        """下载单个文件"""
        local_path, _ = QFileDialog.getSaveFileName(
            self, "保存文件到", os.path.basename(oss_key)
        )
        if not local_path:
            return

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_bar.showMessage("下载中...")
        self._set_buttons_enabled(False)

        self.worker = OssWorker(
            self.client.download_file,
            oss_key, local_path,
        )
        self.worker.finished.connect(lambda r: self._on_download_done(r, local_path))
        self.worker.error.connect(self._on_error)
        self.worker.progress.connect(self._on_progress)
        self.worker.start()

    def _on_download_done(self, result: dict, local_path: str):
        self.progress_bar.setVisible(False)
        self._set_buttons_enabled(True)
        self.status_bar.showMessage(f"下载完成: {local_path}")
        QMessageBox.information(self, "下载完成", f"文件已保存到:\n{local_path}")

    def _download_selected(self):
        """下载选中的文件"""
        selected = self._get_selected_items()
        files = [s for s in selected if s["type"] == "file"]
        if not files:
            QMessageBox.warning(self, "提示", "请先选择要下载的文件")
            return
        # 选择保存目录
        save_dir = QFileDialog.getExistingDirectory(self, "选择保存目录")
        if not save_dir:
            return
        for f in files:
            local_path = os.path.join(save_dir, os.path.basename(f["key"]))
            try:
                self.client.download_file(f["key"], local_path)
            except Exception as e:
                QMessageBox.warning(self, "下载失败", f"{f['key']}\n{str(e)}")
        QMessageBox.information(self, "下载完成", f"已下载 {len(files)} 个文件")
        self._refresh()

    def _delete_selected(self):
        """删除选中的文件/目录"""
        selected = self._get_selected_items()
        if not selected:
            QMessageBox.warning(self, "提示", "请先选择要删除的项目")
            return
        self._do_delete_selected(selected)

    def _do_delete_selected(self, selected: list):
        """执行删除"""
        names = [s["name"] for s in selected]
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除以下项目吗？\n\n" + "\n".join(names),
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        for s in selected:
            try:
                if s["type"] == "dir":
                    self.client.delete_directory(s["key"])
                else:
                    self.client.delete_file(s["key"])
            except Exception as e:
                QMessageBox.warning(self, "删除失败", f"{s['key']}\n{str(e)}")
        self._refresh()

    def _create_directory(self):
        """创建目录"""
        name, ok = QInputDialog.getText(self, "创建目录", "目录名（自动补 / 结尾）:")
        if not ok or not name.strip():
            return
        dir_path = self._current_prefix + name.strip()
        try:
            result = self.client.create_directory(dir_path)
            self.status_bar.showMessage(f"目录已创建: {dir_path}")
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "创建失败", str(e))

    def _delete_directory(self):
        """删除指定目录"""
        prefix = self._current_prefix
        if not prefix:
            reply = QMessageBox.question(self, "危险操作", "当前在根目录，确定删除整个 Bucket 所有文件？",
                                         QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        try:
            result = self.client.delete_directory(prefix)
            QMessageBox.information(self, "删除完成", f"已删除 {result['deleted_count']} 个对象")
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "删除失败", str(e))

    def _rename_selected(self):
        """重命名选中文件"""
        selected = self._get_selected_items()
        if not selected or len(selected) != 1:
            QMessageBox.warning(self, "提示", "请选择一个文件进行重命名")
            return
        s = selected[0]
        if s["type"] == "dir":
            QMessageBox.warning(self, "提示", "暂不支持重命名目录")
            return

        new_name, ok = QInputDialog.getText(
            self, "重命名", "新文件名:", text=s["name"]
        )
        if not ok or not new_name.strip() or new_name.strip() == s["name"]:
            return

        dst_key = self._current_prefix + new_name.strip()
        try:
            self.client.rename_object(s["key"], dst_key)
            self.status_bar.showMessage(f"已重命名: {s['name']} -> {new_name}")
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "重命名失败", str(e))

    def _presign_selected(self):
        """为选中文件生成签名 URL"""
        selected = self._get_selected_items()
        if not selected or len(selected) != 1:
            QMessageBox.warning(self, "提示", "请选择一个文件")
            return
        s = selected[0]
        self._show_presign_dialog(s["key"])

    def _show_presign_dialog(self, key: str):
        """显示签名 URL 对话框"""
        dlg = PresignDialog(self)
        dlg.set_key(key)

        def on_generate():
            try:
                expires = int(dlg.expires_edit.text())
            except ValueError:
                expires = 3600
            try:
                result = self.client.get_presigned_url(key, expires, "GET")
                dlg.url_edit.setText(result["url"])
            except Exception as e:
                QMessageBox.critical(self, "错误", str(e))

        dlg.generate_btn.clicked.connect(on_generate)
        dlg.exec_()

    # ── Bucket 信息 ───────────────────────────────────────────────

    def _show_bucket_info(self):
        try:
            info = self.client.get_bucket_info()
            msg = "\n".join(f"{k}: {v}" for k, v in info.items())
            QMessageBox.information(self, "Bucket 信息", msg)
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

    def _show_bucket_stat(self):
        try:
            stat = self.client.get_bucket_stat()
            msg = "\n".join(f"{k}: {v}" for k, v in stat.items())
            QMessageBox.information(self, "存储统计", msg)
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

    # ── 辅助方法 ──────────────────────────────────────────────────

    def _get_selected_items(self) -> list:
        """获取当前选中的表格项"""
        rows = set(idx.row() for idx in self.table.selectedIndexes())
        selected = []
        for r in rows:
            item = self.table.item(r, 0)
            if item:
                selected.append({
                    "key": item.data(Qt.UserRole),
                    "type": item.data(Qt.UserRole + 1),
                    "name": item.text(),
                })
        return selected

    def _on_progress(self, sent: int, total: int):
        """更新进度条"""
        self.progress_bar.setVisible(True)
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(sent)
        self.status_bar.showMessage(f"传输中... {fmt_size(sent)} / {fmt_size(total)}")

    def _set_buttons_enabled(self, enabled: bool):
        """禁用/启用所有按钮"""
        for btn in [
            self.btn_upload, self.btn_download, self.btn_delete,
            self.btn_mkdir, self.btn_rmdir, self.btn_rename,
            self.btn_presign, self.btn_refresh, self.btn_info,
            self.btn_stat, self.btn_back, self.btn_forward, self.btn_root,
        ]:
            btn.setEnabled(enabled)
