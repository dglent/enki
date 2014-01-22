import unittest
import logging
import os
import sys
import threading
import shutil
import time
import tempfile
import subprocess

import sip
sip.setapi('QString', 2)

from PyQt4.QtCore import Qt, QTimer, QEventLoop
from PyQt4.QtGui import QApplication, QDialog, QKeySequence
from PyQt4.QtTest import QTest


import qutepart

sys.path.insert(0, os.path.join(os.path.abspath(os.path.dirname(__file__)), ".."))

from enki.widgets.dockwidget import DockWidget
import enki.core.defines
enki.core.defines.CONFIG_DIR = tempfile.gettempdir()
from enki.core.core import core

logging.basicConfig(level=logging.ERROR)
logging.getLogger('qutepart').removeHandler(qutepart.consoleHandler)


class DummyProfiler:
    """Dummy profiler is used to run core without profiling"""
    def stepDone(self, description):
        pass

    def printInfo(self):
        pass


def _processPendingEvents(app):
    """Process pending application events.
    Timeout is used, because on Windows hasPendingEvents() always returns True
    """
    t = time.time()
    while app.hasPendingEvents() and (time.time() - t < 0.1):
        app.processEvents()


def inMainLoop(func, *args):
    """Decorator executes test method in the QApplication main loop.
    QAction shortcuts doesn't work, if main loop is not running.
    Do not use for tests, which doesn't use main loop, because it slows down execution.
    """
    def wrapper(*args):
        self = args[0]

        def execWithArgs():
            core.mainWindow().show()
            QTest.qWaitForWindowShown(core.mainWindow())
            _processPendingEvents(self.app)

            try:
                func(*args)
            finally:
                _processPendingEvents(self.app)
                self.app.quit()

        QTimer.singleShot(0, execWithArgs)

        self.app.exec_()

    wrapper.__name__ = func.__name__  # for unittest test runner
    return wrapper


def _cmdlineUtilityExists(cmdlineArgs):
    try:
        subprocess.call(cmdlineArgs, stdout=subprocess.PIPE)
    except OSError as e:
        if e.errno == os.errno.ENOENT:
            return False

    return True


def requiresCmdlineUtility(command):
    """A decorator: a test requires a command.
    The command will be splitted if contains spaces
    """
    def inner(func):
        def wrapper(*args, **kwargs):
            cmdlineArgs = command.split()
            if not _cmdlineUtilityExists(cmdlineArgs):
                self = args[0]
                self.fail('{} command not found. Can not run the test without it'.format(cmdlineArgs[0]))
            return func(*args, **kwargs)
        return wrapper
    return inner


papp = QApplication(sys.argv)
class TestCase(unittest.TestCase):
    app = papp

    TEST_FILE_DIR = os.path.join(tempfile.gettempdir(), 'enki-tests')

    EXISTING_FILE = os.path.join(TEST_FILE_DIR, 'existing_file.txt')
    EXISTING_FILE_TEXT = 'hi\n'

    def _cleanUpFs(self):
        jsonTmp = os.path.join(tempfile.gettempdir(), 'enki.json')
        try:
            os.unlink(jsonTmp)
        except OSError as e:
            pass

        try:
            shutil.rmtree(self.TEST_FILE_DIR)
        except OSError as e:
            pass


    def setUp(self):
        self._finished = False
        self._cleanUpFs()
        try:
            os.mkdir(self.TEST_FILE_DIR)
        except OSError as e:
            pass

        with open(self.EXISTING_FILE, 'w') as f:
            f.write(self.EXISTING_FILE_TEXT)

        os.chdir(self.TEST_FILE_DIR)

        core.init(DummyProfiler())

    def tearDown(self):
        self._finished = True

        for document in core.workspace().documents():
            document.qutepart.text = ''  # clear modified flag, avoid Save Files dialog

        core.workspace().closeAllDocuments()
        core.term()
        self._cleanUpFs()

    def keyClick(self, key, modifiers=Qt.NoModifier, widget=None):
        """Alias for ``QTest.keyClick``.

        If widget is none - focused widget will be keyclicked"""
        if widget is not None:
            widget = self.app.focusWidget()

        if isinstance(key, basestring):
            assert modifiers == Qt.NoModifier, 'Do not set modifiers, if using text key'
            code = QKeySequence(key)[0]
            key = Qt.Key(code & 0x00ffffff)
            modifiers = Qt.KeyboardModifiers(code & 0xff000000)

        QTest.keyClick(widget, key, modifiers)

    def keyClicks(self, text, modifiers=Qt.NoModifier, widget=None):
        """Alias for ``QTest.keyClicks``.

        If widget is none - focused widget will be keyclicked"""
        if widget is not None:
            QTest.keyClicks(widget, text, modifiers)
        else:
            QTest.keyClicks(self.app.focusWidget(), text, modifiers)

    def createFile(self, name, text):
        """Create file in TEST_FILE_DIR.

        File is opened
        """
        path = os.path.join(self.TEST_FILE_DIR, name)
        with open(path, 'w') as file_:
            file_.write(text)

        return core.workspace().openFile(path)

    def _findDialog(self):
        for widget in self.app.topLevelWidgets():
            if widget.isVisible() and isinstance(widget, QDialog):
                return widget
        else:
            return None

    def openDialog(self, openDialogFunc, runInDialogFunc):
        """Open dialog by executing ``openDialogFunc`` and run ``runInDialogFunc``.
        Dialog is passed as a parameter to ``runInDialogFunc``
        """
        DELAY = 20
        ATTEMPTS = 50

        def isDialogsChild(dialog, widget):
            if widget is None:
                return False

            return widget is dialog or \
                   isDialogsChild(dialog, widget.parentWidget())

        def timerCallback(attempt):
            if self._finished:
                return

            dialog = self._findDialog()

            if dialog is not None and \
               isDialogsChild(dialog, self.app.focusWidget()):
                runInDialogFunc(dialog)
            else:
                if attempt < ATTEMPTS:
                    QTimer.singleShot(20, lambda: timerCallback(attempt + 1))
                else:
                    self.fail("Dialog not found")

        QTimer.singleShot(20, lambda: timerCallback(1))
        openDialogFunc()

    def openSettings(self, runInDialogFunc):
        """Open Enki settings dialog and run ``runInDialogFunc``.
        Dialog is passed as a parameter to ``runInDialogFunc``
        """
        return self.openDialog(core.actionManager().action("mSettings/aSettings").trigger,
                               runInDialogFunc)

    def sleepProcessEvents(self, delay):
        end = time.time() + delay
        while time.time() < end:
            self.app.processEvents()
            time.sleep(0.01)

    def findDock(self, windowTitle):
        for dock in core.mainWindow().findChildren(DockWidget):
            if dock.windowTitle() == windowTitle:
                return dock
        else:
            self.fail('Dock {} not found'.format(windowTitle))
            
# This function waits up to timeout_ms for sender_signal to be emitted.
# It returns True if the sender_signal was emitted; otherwise, it returns False.
# This function was inspired by http://stackoverflow.com/questions/2629055/qtestlib-qnetworkrequest-not-executed/2630114#2630114.
def waitForSignal(sender_signal, timeout_ms = 1000):
    # Create a single-shot timer. Could use QTimer.singleShot(),
    # but don't know how to cancel this / disconnect it.
    timer = QTimer()
    timer.setSingleShot(True)

    # Run an event loop to wait for either the sender_signal
    # or the timer's timeout signal.
    loop = QEventLoop()
    sender_signal.connect(loop.quit)
    timer.timeout.connect(loop.quit)
    timer.start(timeout_ms)
    loop.exec_()

    # Clean up: don't allow the timer to call loop after this
    # function exits, which would produce "interesting" behavior.
    ret = timer.isActive()
    timer.stop()
    # Stopping the timer may not cancel timeout signals in the
    # event queue. Disconnect the signal to be sure that loop
    # will never receive a timeout after the function exits.
    # Likewise, disconnect the sender_signal for the same reason.
    sender_signal.disconnect(loop.quit)
    timer.timeout.disconnect(loop.quit)
    
    return ret