import sys
import threading

sum = 0
m = threading.Lock()
cv = threading.Condition(m)

def f():
  global sum
  for i in range(10 * 1000 * 1000):
    sum += 1

  m.acquire()
  cv.notify()
  m.release()

def main(argv):
  t = threading.Thread(target=f)
  t.start()

  m.acquire()
  cv.wait()
  print('Sum', sum)
  m.release()

  t.join()
if __name__ == '__main__':
  main(sys.argv)
