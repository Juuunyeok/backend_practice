#!/usr/bin/python3
from flask import Flask
from flask import request
from flask import make_response, jsonify

app = Flask(__name__)

@app.route('/<arg1>/<op>/<arg2>', methods=['GET'])
def calculate_get(arg1, op, arg2):
  if op == '+':
    result = int(arg1)+int(arg2)
    resp = make_response(jsonify({"status": "200", "result":result}))
  elif op == '-':
    result = int(arg1)-int(arg2)
    resp = make_response(jsonify({"status": "200", "result":result}))
  elif op == '*':
    result = int(arg1)*int(arg2)
    resp = make_response(jsonify({"status": "200", "result":result}))
  else:
    resp = make_response(jsonify({"status":"400 Bad Request", "error":"error"}))
  
  return resp

@app.route('/', methods=['POST'])
def calculate_post():
  arg1 = request.get_json().get('arg1')
  arg2 = request.get_json().get('arg2')
  op = request.get_json().get('op')

  if arg1 is None or arg2 is None or op is None:
    return make_response(jsonify({"status": "400 Bad Request", "error": "no data"}), 400)


  if op == '+':
    result = int(arg1)+int(arg2)
    resp = make_response(jsonify({"status": "200", "result":result}))
  elif op == '-':
    result = int(arg1)-int(arg2)
    resp = make_response(jsonify({"status": "200", "result":result}))
  elif op == '*':
    result = int(arg1)*int(arg2)
    resp = make_response(jsonify({"status": "200", "result":result}))
  else:
    resp = make_response(jsonify({"status":"400 Bad Request", "error":"error"}))
  
  return resp


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=20212)