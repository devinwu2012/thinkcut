# -*- coding: utf-8 -*-
"""测试不碰真实的达芬奇写入锁：B-roll、花字、剪口播交付的写入锁都认环境变量 THINKCUT_RESOLVE_LOCK。"""
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).absolute().parents[3]))
sys.path.insert(0,str(Path(__file__).absolute().parents[3]/'bin'))
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch
from engine.broll.live import workspace
from engine.fx import ledger

REAL=Path.home()/'.cache/ai-talking-head-edit/resolve-write.lock'


def real_state():
    """真实锁文件的内容和修改时间；只读，不加锁、不写。"""
    try:return REAL.read_bytes(),REAL.stat().st_mtime_ns
    except OSError:return None


class WriteLockEnvTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.lock=self.root/'锁'/'resolve-write.lock';self.home=self.root/'home'
        for p in (patch.dict(os.environ,{'THINKCUT_RESOLVE_LOCK':str(self.lock)}),patch.object(Path,'home',return_value=self.home),
                  patch.object(ledger,'LOCK_PATH',None)):
            p.start();self.addCleanup(p.stop)
        self.default=self.home/'.cache/ai-talking-head-edit/resolve-write.lock'

    def test_broll_default_lock_follows_env(self):
        with workspace.write_lock():self.assertEqual(self.lock.read_text(),str(os.getpid()))
        self.assertFalse(self.default.exists())
        with workspace.write_lock():                                  # 是同一把：占着时花字拿不到
            with self.assertRaisesRegex(RuntimeError,'另一处正在写达芬奇'):
                with ledger.write_lock():pass

    def test_fx_lock_follows_env(self):
        with ledger.write_lock():self.assertEqual(self.lock.read_text(),str(os.getpid()))
        self.assertFalse(self.default.exists())

    def test_explicit_path_still_wins_and_default_unchanged_without_env(self):
        other=self.root/'other.lock'
        with workspace.write_lock(other):self.assertTrue(other.exists())
        self.assertFalse(self.lock.exists())
        with patch.dict(os.environ,{'THINKCUT_RESOLVE_LOCK':''}):        # 没设环境变量：还是本机默认那把（这里的“本机”是临时目录）
            with workspace.write_lock():self.assertEqual(self.default.read_text(),str(os.getpid()))
        self.assertFalse(self.lock.exists())

    def test_real_lock_untouched_by_every_writer(self):
        """三处写入锁（B-roll、花字、剪口播交付）拿一遍，真实锁文件的内容和修改时间都没变。"""
        import edit_project
        before=real_state()
        with workspace.write_lock():pass
        with ledger.write_lock():pass
        with edit_project.resolve_write_lock(self.root/'片子'/'审核'):pass
        self.assertIn('口播剪辑 deliver',self.lock.read_text())      # 都写进了临时锁
        self.assertEqual(real_state(),before)

    def test_fake_resolve_tests_default_to_a_temporary_lock(self):
        """用假达芬奇的测试（Session 写入不传锁路径）导入 _fake_resolve 时就已经换成临时锁。另起进程查，不影响别的测试。"""
        import subprocess
        env={k:v for k,v in os.environ.items() if k!='THINKCUT_RESOLVE_LOCK'}
        code="import os;from engine.broll.live import _fake_resolve;p=os.environ['THINKCUT_RESOLVE_LOCK'];print(p);print(os.path.exists(p))"
        out=subprocess.run([sys.executable,'-X','utf8','-c',code],cwd=str(Path(__file__).absolute().parents[3]),env=env,capture_output=True,text=True,check=True).stdout.split()
        self.assertEqual(out[1],'True');self.assertNotEqual(Path(out[0]),REAL);self.assertIn('broll-test-lock-',out[0])


if __name__=='__main__':unittest.main()
