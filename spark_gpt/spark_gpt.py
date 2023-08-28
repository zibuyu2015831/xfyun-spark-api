from wsgiref.handlers import format_date_time
import websocket  # 使用websocket_client
from collections import OrderedDict
from urllib.parse import urlencode
from urllib.parse import urlparse
import _thread as thread
from time import mktime
import hashlib
import datetime
import logging
import base64
import hmac
import json
import ssl
import os

BASE_PATH = os.path.dirname(os.path.abspath(__file__))


# 生成日志记录对象
def get_logger(log_name="app_log", filename='运行日志.log', fmt=None, if_console=True):
    today = datetime.date.today()
    formatted_date = today.strftime("%Y%m%d")

    if not fmt:
        fmt = "%(asctime)s|%(levelname)8s|代码行号：%(lineno)4s|%(message)s"

    # 创建一个Logger，名称是app_log
    logger = logging.getLogger(log_name)

    # 设置为日志输出级别
    logger.setLevel(logging.DEBUG)

    # 创建formatter，并设置formatter的格式
    formatter = logging.Formatter(fmt=fmt, datefmt="%Y-%m-%d %H:%M:%S", )

    if if_console:
        # 创建终端输出handler，为其设置格式，并添加到logger中
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    # 创建文件输出handler，为其设置格式，并添加到logger中
    log_dir = os.path.join(BASE_PATH, "log_file")
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    file_path = os.path.join(log_dir, f"{formatted_date}-{filename}")
    file_handler = logging.FileHandler(filename=file_path, mode='a', encoding='utf8', delay=False)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


class WsParam(object):
    """
    该类适合讯飞星火大部分接口的调用。
    输入 app_id, api_key, api_secret, spark_url以初始化，
    create_url方法返回接口url
    """

    # 初始化
    def __init__(self, app_id, api_key, api_secret, spark_url):
        self.app_id = app_id
        self.api_key = api_key
        self.api_secret = api_secret
        self.host = urlparse(spark_url).netloc
        self.path = urlparse(spark_url).path
        self.spark_url = spark_url

    # 生成url
    def create_url(self):
        # 生成RFC1123格式的时间戳
        now = datetime.datetime.now()
        date = format_date_time(mktime(now.timetuple()))

        # 拼接字符串
        signature_origin = "host: " + self.host + "\n"
        signature_origin += "date: " + date + "\n"
        signature_origin += "GET " + self.path + " HTTP/1.1"

        # 进行hmac-sha256进行加密
        signature_sha = hmac.new(self.api_secret.encode('utf-8'), signature_origin.encode('utf-8'),
                                 digestmod=hashlib.sha256).digest()

        signature_sha_base64 = base64.b64encode(signature_sha).decode(encoding='utf-8')

        authorization_origin = f'api_key="{self.api_key}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature_sha_base64}"'

        authorization = base64.b64encode(authorization_origin.encode('utf-8')).decode(encoding='utf-8')

        # 将请求的鉴权参数组合为字典
        v = {
            "authorization": authorization,
            "date": date,
            "host": self.host
        }
        # 拼接鉴权参数，生成url
        url = self.spark_url + '?' + urlencode(v)
        # 此处打印出建立连接时候的url,参考本demo的时候可取消上方打印的注释，比对相同参数时生成的url与自己代码生成的url是否一致
        return url


class SparkGPT(object):
    def __init__(self, prompt=None, language="general"):
        # 从配置文件中读取配置信息
        with open('config.json', 'r', encoding='utf8') as f:
            self.conf_dict = json.load(f)
        # 下面为必要配置，如果从配置文件中获取不到的话，直接报错
        self.spark_info = self.conf_dict.get("spark_info")
        self.app_id = self.spark_info['app_id']
        self.api_key = self.spark_info['api_key']
        self.api_secret = self.spark_info['api_secret']
        self.domain = self.spark_info['domain']
        self.Spark_url = self.spark_info['Spark_url']

        # 下面是非必要配置，如果从配置文件中获取不到的话，则使用默认值
        self.temperature = float(self.spark_info.get('temperature', 0.5))
        self.top_k = int(self.spark_info.get('top_k', 4))
        self.max_tokens = int(self.spark_info.get('max_tokens', 2048))
        self.error_code_dict = self.spark_info.get('error_code_dict', {})
        self.flow_print = False  # 多轮会话中，是否使用流式打印

        # 获取配置文件中的预设prompt
        self.prompt = self.spark_info.get('prompt', '')
        # 如果实例化时，用户传入了prompt，则优先使用用户的prompt
        if prompt:
            self.prompt = prompt

        self.language = language  # 文本语言设置，用于计算token总限制数。
        if self.language == "chinese":  # 纯中文文本
            self.max_length = 12000
        elif self.language == "english":  # 纯英文文本
            self.max_length = 6000 * 5  # 假设英文单词平均长度为5
        else:  # 中英文混杂文本，保守起见，设为9000，此值无固定，可改
            self.max_length = 9000

        self.text = []  # 历史会话列表
        self.single_answer_data = None
        self.all_answers_data = []  # 所有答案的流式信息

        self.single_answer = ""  # 用于流式接收回答
        self.error_msg = ""  # 错误文本

        if_console = self.spark_info.get('if_console', False)  # 日志是否同时在终端窗口输出
        self.logger = get_logger(if_console=bool(if_console))  # 日志处理对象

        # tokens计算相关
        self.this_tokens = ""  # 最新次交互，总消耗的tokens数
        self.this_answer_tokens = ""  # 最新次交互，回答部分消耗的tokens数
        self.this_question_tokens = ""  # 本次问题的tokens数，在多轮会话中，也是累计问题tokens数
        self.all_tokens = 0  # 累计消耗tokens总数

    # 设置设置max_tokens，传入[1,4096]之间的数字
    def set_max_tokens(self, value):
        try:
            self.logger.info(f"正在重设max_tokens，传入值为：{value}")
            max_tokens = int(value)

            if 1 <= max_tokens <= 4096:
                self.max_tokens = max_tokens
                self.logger.info(f"max_tokens重设成功，设置后的值为：{max_tokens}")
                return max_tokens
            else:
                self.logger.error(f"max_tokens重设失败，传入值--【{max_tokens}】--超出范围")
                print("max_tokens重设失败，max_tokens取值区间为：[1,4096]，请检查传入值")

        except Exception as e:
            self.logger.error(f"max_tokens重设失败，传入值--【{value}】--非整数")
            print("max_tokens重设失败，传入参数必须是整数")

    def set_top_k(self, value):
        try:
            self.logger.info(f"正在重设top_k，传入值为：{value}")
            top_k = int(value)
            if 1 <= top_k <= 6:
                self.top_k = top_k
                self.logger.info(f"top_k重设成功，设置后的值为：{top_k}")
                return top_k
            else:
                self.logger.error(f"top_k重设失败，传入值--【{top_k}】--超出范围")
                print("top_k重设失败，top_k取值区间为：[1,6]，请检查传入值")

        except Exception as e:
            self.logger.error(f"top_k重设失败，传入值--【{value}】--非整数字")
            print("top_k重设失败，传入参数必须是整数")

    def set_temperature(self, value):
        try:
            self.logger.info(f"正在重设temperature，传入值为：{value}")
            temperature = float(value)
            if 0 <= temperature <= 1:
                self.temperature = temperature
                self.logger.info(f"temperature重设成功，设置后的值为：{temperature}")
                return temperature
            else:
                self.logger.error(f"temperature重设失败，传入值--【{temperature}】--超出范围")
                print("temperature重设失败，temperature取值区间为：[0,1]，请检查传入值")

        except Exception as e:
            self.logger.error(f"temperature重设失败，传入值--【{value}】--非浮点型数字")
            print("temperature重设失败，传入参数必须是浮点型数字")

    def set_language(self, language):

        if language == "chinese":
            self.language = language
            self.max_length = 12000
        elif language == "english":
            self.max_length = 6000 * 5

    def reset_text(self):
        self.text = []

    # 处理流式打印效果
    def handle_flow_print(self, status, content, seq):
        if status == 0:
            self.logger.info("收到第1次回应，内容为：%s" % {content.replace('\n', '\\n')})
            print(content, end="")
        elif status == 1:
            self.logger.info("收到第%d次回应，内容为：%s" % (seq + 1, content.replace('\n', '\\n')))
            print(content, end="")
        elif status == 2:
            self.logger.info("收到最终回应，内容为：%s" % content.replace('\n', '\\n'))
            print(content, end="")
            print()

    # 处理错误码。官方的错误代码信息写在配置文件
    def handle_error_code(self, code):
        # 根据回复的错误代码，显示错误类型
        if str(code) in self.error_code_dict:
            self.error_msg = self.error_code_dict[str(code)]
        else:
            self.error_msg = f"错误代码：【{code}】--- 配置文件中未查询到这个错误代码，请参看讯飞官方文档。"

        return self.error_msg

    # 存储tokens数
    def store_token_num(self, data):
        try:
            self.this_tokens = data['payload']['usage']['text']['total_tokens']  # 本次交互计费的tokens数
            self.this_answer_tokens = data['payload']['usage']['text']['completion_tokens']  # 本次回答的tokens数
            self.this_question_tokens = data['payload']['usage']['text']['prompt_tokens']  # 本次问题的tokens数

            self.all_tokens += self.this_tokens

        except Exception as e:
            self.error_msg = f"此次问答，tokens计算过程出现问题。【错误提示：{self.error_msg}】"

    def get_answer(self):
        if self.error_msg:
            print(self.error_msg)
        return self.all_answers_data[-1]['answer']

    # 处理返回数据
    def on_message(self, ws, message):
        self.error_msg = ""  # 重置错误信息

        data = json.loads(message)
        code = data['header']['code']

        if code != 0:
            ws.close()  # 请求错误，则关闭socket
            self.logger.critical(f'回答获取失败，响应信息反序列化之后为： {data}')
            error_msg = self.handle_error_code(code)
            self.logger.critical(f'【错误提示】 --- {error_msg}')
            return
        else:
            choices = data["payload"]["choices"]
            seq = choices["seq"]  # 服务端是流式返回，seq为返回的数据序号
            status = choices["status"]  # 服务端是流式返回，status用于判断信息是否传送完毕
            content = choices["text"][0]["content"]  # 本次接收到的回答文本

            # 多轮会话中，流式打印接收到的回答
            if self.flow_print:
                self.handle_flow_print(status, content, seq)

            self.single_answer += content

            if status == 0:  # 0代表首次结果
                # self.single_answer_data = OrderedDict()
                self.single_answer_data[seq] = content
            elif status == 1:  # 1代表中间结果
                self.single_answer_data[seq] = content
            elif status == 2:  # 2代表最后一个结果，表示所有数据传送完毕

                # self.logger.info(f"整合所有回应，内容为：{self.single_answer}")
                self.logger.info("整合所有回应，内容为：%s" % self.single_answer.replace('\n', '\\n'))

                self.single_answer_data[seq] = content
                self.single_answer_data["answer"] = self.single_answer
                self.all_answers_data.append(self.single_answer_data)

                self.make_text("assistant", self.single_answer)

                self.single_answer_data = None
                self.single_answer = ""
                self.store_token_num(data)

                ws.close()
                self.logger.info(f"本次通讯关闭")

    # 收到websocket错误的处理
    def on_error(self, ws, error):
        # on_message方法处理接收到的信息，出现任何错误，都会调用这个方法
        self.error_msg = f'通讯连接出错，【错误提示: {error}】'
        self.logger.critical(f'通讯连接出错，【错误提示: {error}】')

    # 收到websocket关闭的处理
    def on_close(self, ws, one, two):
        pass
        # print("通讯完成")

    def get_length(self):
        length = 0
        for content in self.text:
            temp = content["content"]
            length += len(temp)
        self.logger.info(f"文本长度为：{length}")
        return length

    def check_len(self):
        """
        检查文本长度，
        官方规定：所有content内容加一起的tokens需要控制在8192以内。
        1tokens 约等于1.5个中文汉字 或者 0.8个英文单词。
        8192 tokens 约等于12000中文汉字，6000英文单词
        详见官方文档：https://www.xfyun.cn/doc/spark/Web.html#_1-%E6%8E%A5%E5%8F%A3%E8%AF%B4%E6%98%8E
        :return:
        """

        self.logger.info(f"开始循环检查文本长度，预设语言为{self.language}，文本长度限制为{self.max_length}")
        while self.get_length() > self.max_length:
            self.logger.info(f"文本长度超出限制，去除文本第一项")
            del self.text[0]
            self.logger.info(f"去除之后的文本：{json.dumps(self.text)}")
        self.logger.info(f"文本长度满足要求")
        return self.text

    # 处理请求数据
    def gen_params(self):
        # 检查文本总长度是否满足要求
        self.check_len()

        data = {
            "header": {
                "app_id": self.app_id,
                "uid": "1234"
            },
            "parameter": {
                "chat": {
                    # domain为必传参数
                    "domain": self.domain,

                    # 以下为可微调，非必传参数
                    # 注意：官方建议，temperature和top_k修改一个即可
                    "max_tokens": self.max_tokens,  # 默认2048，模型回答的tokens的最大长度，即允许它输出文本的最长字数
                    "temperature": self.temperature,  # 取值为[0,1],默认为0.5。取值越高随机性越强、发散性越高，即相同的问题得到的不同答案的可能性越高
                    "top_k": self.top_k,  # 取值为[1，6],默认为4。从k个候选中随机选择一个（非等概率）
                }
            },
            "payload": {
                "message": {
                    "text": self.text
                }
            }
        }
        return data

    def send(self, ws, *args):
        data = json.dumps(self.gen_params())
        self.logger.info(f"开始尝试建立通讯...")
        self.logger.info(f"此次请求的请求头数据为：{data}")
        ws.send(data)

    # 收到websocket连接建立的处理
    def on_open(self, ws):
        thread.start_new_thread(self.send, (ws,))

    # 处理收到的 websocket消息，出现任何错误，调用on_error方法
    def _run(self, text_list):

        ws_param = WsParam(self.app_id, self.api_key, self.api_secret, self.Spark_url)
        ws_url = ws_param.create_url()

        websocket.enableTrace(False)  # 默认禁用 WebSocket 的跟踪功能
        ws = websocket.WebSocketApp(ws_url, on_message=self.on_message, on_error=self.on_error, on_close=self.on_close,
                                    on_open=self.on_open)
        ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})

    def make_text(self, role, content):

        # 如果是第一次提问，并且用户提供了prompt，或者配置文件中配置了prompt，则问题内容前添加上prompt
        if not self.text and self.prompt:
            self.text.append({"role": role, "content": self.prompt + content})
        else:
            self.text.append({"role": role, "content": content})
        # self.text.append({"role": role, "content": content})
        self.logger.info(f"成功构建文本：{json.dumps(self.text)}")

        return self.text

    def count_money(self, tokens):
        if self.domain == "generalv2":
            return f"{0.00032 * tokens} 元"
        elif self.domain == "generalv1":
            return f"{0.00018 * tokens} 元"
        else:
            return "（计算出错）"

    def talk(self):
        self.flow_print = True
        self.reset_text()  # 清除历史会话数据
        while True:
            user_input = input("\n" + "请输入问题（输入q则退出）:")

            self.single_answer_data = OrderedDict()  # 创建有序字典，用于存放用户的问题与对应答案的流式信息
            self.single_answer_data['question'] = user_input  # 存放用户问题

            if user_input == "q":
                self.logger.info(f"用户选择退出，此次多轮会话结束。")
                print(f"本次交互中，累计消耗tokens：{self.all_tokens}")
                print(f"根  据 官 方 价 格，费用为：{self.count_money(self.all_tokens)}")
                break
            else:
                self.logger.info(f"收到用户提问：【{user_input}】。开始构建与星火大模型通讯文本...")

            print("星火大模型:", end="")
            self.make_text("user", user_input)
            self._run(self.text)

    def ask(self, question, flow_print=False):
        self.logger.info(f"收到用户提问：【{question}】。开始构建与星火大模型通讯文本...")  # 日志记录
        self.reset_text()  # 清除历史会话数据
        self.single_answer_data = OrderedDict()  # 创建有序字典，用于存放用户的问题与对应答案的流式信息
        self.single_answer_data['question'] = question  # 存放用户问题

        self.flow_print = flow_print  # 是否流式打印，ask默认为False
        if self.flow_print:
            print("星火大模型:", end="")

        self.make_text("user", question)
        self._run(self.text)
        answer = self.get_answer()
        return answer
