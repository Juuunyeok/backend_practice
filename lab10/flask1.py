#!/usr/bin/python3

from flask import Flask

app = Flask(__name__)

@app.route('/')
def hello_world():
  return 'hello, world!'

@app.route('/bad')
def bad_world():
  return 'Bad, world!'

@app.route('/good')
def good_world():
  return 'Good, world!'

if __name__ == '__main__':
  app.run(host='0.0.0.0', port = 20212)