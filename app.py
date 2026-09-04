"""This python file will do the AutoClass job."""
import logging
import os
import sys
import time

from selenium import webdriver
from selenium.common import TimeoutException
from selenium.webdriver import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as ec
from selenium.webdriver.support.ui import WebDriverWait

import utilities as utils

config = None
driver = None


class RegistrationPhase(Exception):
    """Raised when the course system is in the registration phase,
    which means realtime add/drop is not open yet."""


class SeatQueryNoResponse(Exception):
    """Raised when clicking the seat-query button pops no alert."""


def driver_send_keys(locator, key):
    """Send keys to element.

    :param locator: Locator of element.
    :param key: Keys to send.
    """
    WebDriverWait(driver, 10).until(ec.presence_of_element_located(locator)).send_keys(key)


def driver_click(locator):
    """Click element.

    :param locator: Locator of element.
    """
    WebDriverWait(driver, 10).until(ec.presence_of_element_located(locator)).click()


def driver_screenshot(locator, path):
    """Take screenshot of element.

    :param locator: Locator of element.
    :param path: Path to save screenshot.
    """
    WebDriverWait(driver, 10).until(ec.presence_of_element_located(locator)).screenshot(path)


def driver_get_text(locator):
    """Get text of element.

    :param locator: Locator of element.
    :return: Text of element.
    """
    return WebDriverWait(driver, 10).until(ec.presence_of_element_located(locator)).text


def login():
    """Login to FCU course system."""
    driver.get('https://course.fcu.edu.tw/')
    driver_send_keys((By.XPATH, '//*[@id="ctl00_Login1_RadioButtonList1_0"]'), Keys.SPACE)
    driver_send_keys((By.ID, "ctl00_Login1_UserName"), config.get("username"))
    driver_send_keys((By.ID, "ctl00_Login1_Password"), config.get("password"))
    driver_screenshot((By.ID, "ctl00_Login1_Image1"), "captcha.png")
    driver_send_keys((By.ID, "ctl00_Login1_vcode"), utils.get_ocr_answer("captcha.png"))
    driver_click((By.ID, "ctl00_Login1_LoginButton"))
    try:
        WebDriverWait(driver, 1).until(ec.presence_of_element_located((By.ID, "ctl00_btnLogout")))
    except TimeoutException:
        logging.warning("Login Failed, relog now.")
        login()
    logging.info("Login Success. Start auto classing...")
    auto_class(config.get("class_ids"))


def auto_class(class_ids):
    """Auto join class script.

    :param class_ids: List of class ids to join.
    """
    while class_ids:
        for class_id in class_ids[:]:  # create a copy of class_ids for iteration
            driver_click((By.ID, "ctl00_MainContent_TabContainer1_tabSelected_Label3"))
            driver_send_keys((By.ID, "ctl00_MainContent_TabContainer1_tabSelected_tbSubID"),
                             class_id)

            # query remain position
            driver_click((By.XPATH,
                          "//*[@id='ctl00_MainContent_TabContainer1_tabSelected_gvToAdd']/tbody/tr[2]/td[8]/input"))
            try:
                WebDriverWait(driver, 10).until(ec.alert_is_present())
            except TimeoutException:
                raise SeatQueryNoResponse(class_id) from None
            alert = driver.switch_to.alert
            if '登記人數' in alert.text:
                raise RegistrationPhase(alert.text)
            remain_pos = int(alert.text.strip('剩餘名額/開放名額：').split(" /")[0])
            logging.info("課程" + class_id + ": " + alert.text)
            alert.accept()

            if not remain_pos == 0:
                driver_click((By.XPATH,
                              "//*[@id='ctl00_MainContent_TabContainer1_tabSelected_gvToAdd']/tbody/tr[2]/td[1]/input"))
                if driver_get_text((By.XPATH,
                                    "//*[@id='ctl00_MainContent_TabContainer1_tabSelected_lblMsgBlock']/span")) == "加選成功":
                    logging.info("成功加選課程：" + class_id)
                    class_ids.remove(class_id)
                else:
                    logging.warning(
                        "課程" + class_id + ": 加選失敗, 請確認是否已加選或衝堂/超修, 也可能被其他機器人搶走了..")

            # Reload the page so the next query starts from a clean state.
            # After a query the page still holds the previous result grid and
            # the previous id in the textbox. Typing the next id re-renders the
            # grid while the old query button is being clicked
            # (StaleElementReferenceException), or the click lands on the old
            # button and queries the wrong course.
            driver.get(driver.current_url)


def start():
    """Run one attempt: launch the browser, login and start auto classing."""
    global driver
    options = webdriver.ChromeOptions()
    if config.get("headless"):
        options.add_argument('--headless')
    driver = webdriver.Chrome(options=options)
    driver.maximize_window()
    login()


def quit_driver():
    """Close the browser, ignoring errors if it is already gone."""
    global driver
    if driver:
        try:
            driver.quit()
        except Exception:
            pass
        driver = None


def main():
    global config
    os.makedirs('./logs', exist_ok=True)
    log_path = time.strftime('./logs/logs-%Y%m%d-%H%M%S.txt')
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(log_path, encoding='utf-8')])
    config = utils.read_config()
    no_alert_times = 0
    while True:
        try:
            start()
        except KeyboardInterrupt:
            logging.info("使用者中斷程式, 正在關閉...")
            quit_driver()
            sys.exit(130)
        except RegistrationPhase as error:
            logging.error("目前選課系統為「登記」階段 (%s), 尚未開放即時加選, "
                          "程式停止執行, 請於加退選(即時選課)期間再使用!", error)
            quit_driver()
            sys.exit(2)
        except SeatQueryNoResponse as error:
            no_alert_times += 1
            if no_alert_times >= 3:
                logging.error("連續%s次查詢課程名額都沒有跳出名額視窗, "
                              "可能目前為「登記」階段或選課系統頁面已改版, 程式停止執行!",
                              no_alert_times)
                quit_driver()
                sys.exit(2)
            logging.warning("查詢課程 %s 的名額時沒有跳出名額視窗 (第%s次), "
                            "5秒後自動重新啟動... (按 Ctrl+C 可離開)", error, no_alert_times)
            quit_driver()
            try:
                time.sleep(5)
            except KeyboardInterrupt:
                sys.exit(130)
            continue
        except Exception:
            no_alert_times = 0
            logging.exception("程式發生錯誤, 5秒後自動重新啟動... (按 Ctrl+C 可離開)")
            quit_driver()
            try:
                time.sleep(5)
            except KeyboardInterrupt:
                sys.exit(130)
            continue
        quit_driver()
        logging.info("All classes joined.")
        sys.exit(0)


if __name__ == "__main__":
    main()
