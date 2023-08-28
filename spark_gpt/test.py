from spark_speaker import SparkSpeaker

# 1. 直接实例化SparkSpeaker即可，实例化时可以传入一个prompt
speaker = SparkSpeaker("接下来我会给你发送一个文案，请你以伴侣的口吻帮我润色一下，加上合适的称呼")

# 2. 单次询问
answer = speaker.ask("今生今世有缘和你在一起，每一分，每一秒都是幸福，都是老天恩赐的福祉。")
print(answer)
# 输出示例：
# 亲爱的，我知道我们的相遇是缘分所赐，而能够和你在一起，每一分、每一秒都是我今生今世的幸福。这份幸福，是我一生最珍贵的宝藏。
# 谢谢你为我带来的一切美好，我愿意与你一起，永远珍惜这份恩赐的福祉。

# speaker.ask("今生今世有缘和你在一起，每一分，每一秒都是幸福，都是老天恩赐的福祉。", flow_print=True)
# ask方法也可传入一个flow_print参数，值为True或False。该值为True时，系统会在终端窗口流式打印接收到的回应


# 3. 多轮对话
speaker.talk()

print(speaker.text)  # text中，存放了会话记录
