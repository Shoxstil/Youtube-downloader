import telebot
from telebot.types import *
from pytube import YouTube
import random

import os

bot = telebot.TeleBot(token='5586784290:AAG9OxsQ9MjdkDzx0uxEDsja8jYODRncFWg')

@bot.message_handler(commands=['start'])
def command_start(message):
	try:
		bot.send_sticker(chat_id=message.from_user.id,sticker=r"CAACAgIAAxkBAAEF8DpjMruBxng0hnU6Wq1AEsH991CRPwACQhAAAjPFKUmQDtQRpypKgikE")
		bot.send_message(message.from_user.id , 'Здравствуйте Пользователь!!!')
		bot.send_message(message.from_user.id , 'Этот Бот нужен для скачивания видео с Ютуба!\nЧтобы скачать видео с Ютуба отправь мне ссылку')

															
		
	except:
		message.reply('Для общения с ботом напишите ему в ЛС:\n')
	
@bot.message_handler()
def comand_down(message):
	url = message.text
	yt = YouTube(url)
	if message.text.startswith == 'https://youtube.com/' or 'https://youtu.be/':
		bot.send_message(message.from_user.id , f'Начинаю загрузку видео: {yt.title}\nC канала : [{yt.author}]({yt.channel_url})' , parse_mode='Markdown')

	youtube_down(url,message)
def youtube_down(url ,message):
	chat = message.from_user
	yt = YouTube(url)
	stream = yt.streams.filter(progressive=True , file_extension='mp4')
	filename =	f"{random.randint(1,1000)}.mp4"
	stream.get_highest_resolution().download(filename=filename)
	
	bot.send_video(chat.id, open(f"{filename}", "rb"))
	os.remove(f'{filename}')

bot.polling(none_stop=True)