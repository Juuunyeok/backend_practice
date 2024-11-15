#!/usr/bin/python3
import socket
import sys
import threading
import enum
import errno
import json
import select

from absl import app, flags

import message_pb2 as pb

# 명령줄 인자 설정
FLAGS = flags.FLAGS
flags.DEFINE_integer('port', None, required=True, help='port 번호')
flags.DEFINE_enum('format', 'json', ['json', 'protobuf'], help='메시지 포맷')
flags.DEFINE_integer('workers', 2, help='작업 쓰레드 숫자')

# 전역 변수 및 동기화 객체
shutdown_requested = False

class Receiver(enum.Enum):
    ALL = 0
    ONLY_ME = 1
    EXCEPT_ME = 2

class SocketClosed(RuntimeError):
    pass

class NoTypeFieldInMessage(RuntimeError):
    pass

class UnknownTypeInMessage(RuntimeError):
    def __init__(self, _type):
        self.type = _type

    def __str__(self):
        return str(self.type)

class UserConnection:
    def __init__(self, sock: socket.socket, addr):
        self.sock = sock
        self.addr = addr
        self.pending_data: list[bytes] = []
        self.socket_buffer: bytes = None
        self.current_message_len: int = None
        self.current_protobuf_type: pb.Type.MessageType = None
        self._name: str = None
        self.current_room: 'ChatRoom' = None

    def __str__(self):
        return f'{self.addr}:{self._name}'

    @property
    def name(self):
        return self._name or str(self.addr)

    def receive_data(self):
        received_buff = self.sock.recv(65536)
        if not received_buff:
            raise SocketClosed()

        if FLAGS.verbosity >= 2:
            print(f'  - 클라이언트 [{self}]: recv(): {len(received_buff)}바이트 읽음')

        if not self.socket_buffer:
            self.socket_buffer = received_buff
        else:
            self.socket_buffer += received_buff

        if self.current_message_len is None:
            if len(self.socket_buffer) < 2:
                return False

            self.current_message_len = int.from_bytes(self.socket_buffer[0:2], byteorder='big')
            if FLAGS.verbosity >= 2:
                print(f'  - 클라이언트 [{self}] 다음 메시지 길이: {self.current_message_len}')
            self.socket_buffer = self.socket_buffer[2:]

        if len(self.socket_buffer) < self.current_message_len:
            print(f'Wait more: {len(self.socket_buffer)} < {self.current_message_len}')
            return False

        return True

    def send_system_message(self, text, receiver=Receiver.ALL):
        messages = []
        if FLAGS.format == 'json':
            msg = {
                'type': 'SCSystemMessage',
                'text': text,
            }
            messages.append(msg)
        else:
            msg = pb.Type()
            msg.type = pb.Type.MessageType.SC_SYSTEM_MESSAGE
            messages.append(msg)

            msg = pb.SCSystemMessage()
            msg.text = text
            messages.append(msg)

        if receiver == Receiver.ONLY_ME:
            self.send_messages(messages)
        else:
            assert self.current_room
            with rooms_mutex:
                for user in self.current_room.members:
                    if user != self or receiver == Receiver.ALL:
                        user.send_messages(messages)

    def send_messages(self, messages):
        assert isinstance(messages, list)

        for msg in messages:
            msg_as_str = None
            if FLAGS.format == 'json':
                serialized = bytes(json.dumps(msg), encoding='utf-8')
                msg_as_str = json.dumps(msg)
            else:
                serialized = msg.SerializeToString()
                msg_as_str = str(msg).strip()

            to_send = len(serialized)

            serialized = int.to_bytes(to_send, byteorder='big', length=2) + serialized
            self.pending_data.append(serialized)
            if FLAGS.verbosity >= 1:
                print(f'클라이언트 [{self}]: [S->C:총길이={len(serialized)}바이트] 0x{to_send:04x}(메시지크기) + {msg_as_str}')

    def send_pending_data(self):
        for serialized in self.pending_data:
            offset = 0
            count = 0
            while offset < len(serialized):
                count += 1
                num_sent = self.sock.send(serialized[offset:])
                if num_sent <= 0:
                    raise RuntimeError('Send failed')
                if FLAGS.verbosity >= 2:
                    print(f'  - 클라이언트 [{self}] send() 시도 #{count}: {num_sent}바이트 전송 완료')
                offset += num_sent
        self.pending_data.clear()

    def disconnect(self):
        if self.current_room:
            with rooms_mutex:
                self.current_room.members.remove(self)
                if not self.current_room.members:
                    print(f'방[{self.current_room.room_id}]: 접속 종료로 인한 방폭')
                    del rooms[self.current_room.room_id]
        self.current_room = None

        if self.sock:
            self.sock.close()
        self.sock = None
############

    def handle_message(self):
        assert self.current_message_len <= len(self.socket_buffer)
        serialized = self.socket_buffer[:self.current_message_len]
        self.socket_buffer = self.socket_buffer[self.current_message_len:]
        self.current_message_len = None

        if FLAGS.format == 'json':
            if not serialized:
                print("빈 데이터 수신")
                return

            if FLAGS.verbosity >= 1:
                print(f'클라이언트 [{self}]: [C->S:총길이={len(serialized) + 2}바이트] 0x{len(serialized):04x}(메시지크기) + {serialized.decode("utf-8")}')
            msg = json.loads(serialized)
            msg_type = msg.get('type', None)
            if not msg_type:
                raise NoTypeFieldInMessage()

            if msg_type in json_message_handlers:
                if msg_type != 'CSShutdown':
                    json_message_handlers[msg_type](self, msg)
                else:
                    json_message_handlers[msg_type]()
            else:
                raise UnknownTypeInMessage(msg_type)

        else:
            if self.current_protobuf_type is None:
                msg = pb.Type.FromString(serialized)
                if FLAGS.verbosity >= 1:
                    str_msg = str(msg).strip()
                    print(f'클라이언트 [{self}] [C->S:총길이={len(serialized) + 2}바이트] 0x{len(serialized):04x}(메시지크기) + {str_msg}')
                if msg.type in protobuf_message_parsers and msg.type in protobuf_message_handlers:
                    self.current_protobuf_type = msg.type
                else:
                    raise UnknownTypeInMessage(msg.type)
            else:
                msg = protobuf_message_parsers[self.current_protobuf_type](serialized)
                if FLAGS.verbosity >= 1:
                    str_msg = str(msg).strip()
                    print(f'클라이언트 [{self}] [C->S:총길이={len(serialized) + 2}바이트] 0x{len(serialized):04x}(메시지크기) {"+ " + str_msg if str_msg else ""}')

                try:
                    if self.current_protobuf_type != pb.Type.MessageType.CS_SHUTDOWN:
                        protobuf_message_handlers[self.current_protobuf_type](self, msg)
                    else:
                        protobuf_message_handlers[self.current_protobuf_type]()
                finally:
                    self.current_protobuf_type = None

    def on_cs_name(self, message):
        previous_name = self.name

        # 사용자 이름 업데이트
        self._name = message['name'] if FLAGS.format == 'json' else message.name

        # 시스템 메시지 생성 및 전송
        if self.current_room:
            text = f'{previous_name} 의 이름이 {self._name} 으로 변경되었습니다.'
            receiver = Receiver.ALL
        else:
            text = f'이름이 {self._name} 으로 변경되었습니다.'
            receiver = Receiver.ONLY_ME

        self.send_system_message(text, receiver=receiver)


    def on_cs_rooms(self, message):
        rooms_info = []
        with rooms_mutex:
            for room in rooms.values():
                if FLAGS.format == 'json':
                    room_info = {
                        'roomId': room.room_id,
                        'title': room.title,
                        'members': [m.name for m in room.members],
                    }
                    rooms_info.append(room_info)
                else:
                    room_info = pb.SCRoomsResult.RoomInfo()
                    room_info.roomId = room.room_id
                    room_info.title = room.title
                    room_info.members.extend([m.name for m in room.members])
                    rooms_info.append(room_info)

        messages = []
        if FLAGS.format == 'json':
            msg = {
                'type': 'SCRoomsResult',
                'rooms': rooms_info,
            }
            messages.append(msg)
        else:
            msg = pb.Type()
            msg.type = pb.Type.MessageType.SC_ROOMS_RESULT
            messages.append(msg)

            msg = pb.SCRoomsResult()
            msg.rooms.extend(rooms_info)
            messages.append(msg)
        self.send_messages(messages)

    def on_cs_create_room(self, message):
        if self.current_room:
            text = '대화 방에 있을 때는 방을 개설 할 수 없습니다.'
            self.send_system_message(text, receiver=Receiver.ONLY_ME)
            return

        title = message['title'] if FLAGS.format == 'json' else message.title

        with rooms_mutex:
            global next_room_id
            next_room_id = next_room_id + 1 if next_room_id else 1
            new_room = ChatRoom(next_room_id, title)
            new_room.members.append(self)
            rooms[new_room.room_id] = new_room
            self.current_room = new_room

            print(f'방[{new_room.room_id}]: 생성됨. 방제: {new_room.title}')

        text2 = f'방제[{title}] 방에 입장했습니다.'
        self.send_system_message(text2, receiver=Receiver.ONLY_ME)

    def on_cs_join_room(self, message):
        if self.current_room:
            text = '대화 방에 있을 때는 다른 방에 들어갈 수 없습니다.'
            self.send_system_message(text, receiver=Receiver.ONLY_ME)
            return

        room_id = int(message['roomId']) if FLAGS.format == 'json' else int(message.roomId)

        with rooms_mutex:
            room = rooms.get(room_id)
            if room:
                # 방에 참가자 추가 및 현재 방 설정
                room.members.append(self)
                self.current_room = room
                room_title = room.title
            else:
                # 방을 찾을 수 없는 경우
                error_message = '대화방이 존재하지 않습니다.'
                self.send_system_message(error_message, receiver=Receiver.ONLY_ME)
                return

        # 클라이언트에게 방 입장 알림
        success_message = f'방제 [{room_title}] 방에 입장했습니다.'
        self.send_system_message(success_message, receiver=Receiver.ONLY_ME)

        # 다른 방 참가자들에게 입장 알림
        join_message = f'[{self.name}] 님이 입장했습니다.'
        self.send_system_message(join_message, receiver=Receiver.EXCEPT_ME)

    def on_cs_leave_room(self, message):
        if not self.current_room:
            text = '현재 대화방에 들어가 있지 않습니다.'
            self.send_system_message(text, receiver=Receiver.ONLY_ME)
            return
        # 다른 방 멤버들에게 퇴장 알림
        leave_message = f'[{self.name}] 님이 퇴장했습니다.'
        self.send_system_message(leave_message, receiver=Receiver.EXCEPT_ME)

        # room_title = None
        with rooms_mutex:
            room_title = self.current_room.title
            # 현재 사용자 제거
            self.current_room.members.remove(self)
            # 방에 남은 멤버가 없으면 방 삭제
            if not self.current_room.members:
                print(f'방[{self.current_room.room_id}]: 명시적 /leave 명령으로 인한 방폭')
                del rooms[self.current_room.room_id]
            # 현재 방 정보 초기화
            self.current_room = None

        # 클라이언트에게 방 퇴장 알림
        success_message = f'방제 [{room_title}] 대화 방에서 퇴장했습니다.'
        self.send_system_message(success_message, receiver=Receiver.ONLY_ME)

    def on_cs_chat(self, message):
        if not self.current_room:
            text = '현재 대화방에 들어가 있지 않습니다.'
            self.send_system_message(text, receiver=Receiver.ONLY_ME)
            return

        if FLAGS.format == 'json':
            msg = {
                'type': 'SCChat',
                'member': self.name,
                'text': message['text']
            }
            messages = [msg]
        else:
            type_msg = pb.Type(type=pb.Type.MessageType.SC_CHAT)
            chat_msg = pb.SCChat(member=self.name, text=message.text)
            messages = [type_msg, chat_msg]

        # 같은 방의 다른 멤버들에게 메시지 전송
        with rooms_mutex:
            for member in self.current_room.members:
                if member != self:
                    member.send_messages(messages)

class ChatRoom:
    def __init__(self, room_id, title):
        self.room_id = room_id
        self.title = title
        self.members: list[UserConnection] = []

clients_after_processing: list[UserConnection] = []
clients_after_processing_mutex = threading.Lock()

clients_for_processing: list[UserConnection] = []
clients_for_processing_mutex = threading.Lock()
clients_for_processing_cv = threading.Condition(clients_for_processing_mutex)

next_room_id: int = None
rooms: dict[int, ChatRoom] = {}
rooms_mutex = threading.Lock()

def on_cs_shutdown():
    print('서버 중지가 요청됨')
    global shutdown_requested
    shutdown_requested = True
    with clients_for_processing_mutex:
        clients_for_processing_cv.notify_all()

json_message_handlers = {
    'CSName': UserConnection.on_cs_name,
    'CSRooms': UserConnection.on_cs_rooms,
    'CSCreateRoom': UserConnection.on_cs_create_room,
    'CSJoinRoom': UserConnection.on_cs_join_room,
    'CSLeaveRoom': UserConnection.on_cs_leave_room,
    'CSChat': UserConnection.on_cs_chat,
    'CSShutdown': on_cs_shutdown,
}

protobuf_message_handlers = {
    pb.Type.MessageType.CS_NAME: UserConnection.on_cs_name,
    pb.Type.MessageType.CS_ROOMS: UserConnection.on_cs_rooms,
    pb.Type.MessageType.CS_CREATE_ROOM: UserConnection.on_cs_create_room,
    pb.Type.MessageType.CS_JOIN_ROOM: UserConnection.on_cs_join_room,
    pb.Type.MessageType.CS_LEAVE_ROOM: UserConnection.on_cs_leave_room,
    pb.Type.MessageType.CS_CHAT: UserConnection.on_cs_chat,
    pb.Type.MessageType.CS_SHUTDOWN: on_cs_shutdown,
}

protobuf_message_parsers = {
    pb.Type.MessageType.CS_NAME: pb.CSName.FromString,
    pb.Type.MessageType.CS_ROOMS: pb.CSRooms.FromString,
    pb.Type.MessageType.CS_CREATE_ROOM: pb.CSCreateRoom.FromString,
    pb.Type.MessageType.CS_JOIN_ROOM: pb.CSJoinRoom.FromString,
    pb.Type.MessageType.CS_LEAVE_ROOM: pb.CSLeaveRoom.FromString,
    pb.Type.MessageType.CS_CHAT: pb.CSChat.FromString,
    pb.Type.MessageType.CS_SHUTDOWN: pb.CSShutdown.FromString,
}

def message_worker(thread_id):
    print(f'메시지 작업 쓰레드 #{thread_id} 생성')

    while not shutdown_requested:
        with clients_for_processing_mutex:
            while not shutdown_requested and not clients_for_processing:
                clients_for_processing_cv.wait()

            if shutdown_requested:
                break
            client = clients_for_processing.pop(0)

        try:
            client.handle_message()

            with clients_after_processing_mutex:
                clients_after_processing.append(client)

        except RuntimeError as err:
            print('Exception', err)
            client.disconnect()

    print(f'메시지 작업 쓰레드 #{thread_id} 종료')

def main(args):
    global shutdown_requested

    if not FLAGS.port:
        print('서버의 Port 번호를 지정해야 됩니다.')
        sys.exit(2)

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(('0.0.0.0', FLAGS.port))
    server_sock.listen()

    worker_threads: list[threading.Thread] = []
    for i in range(FLAGS.workers):
        thread = threading.Thread(target=message_worker, args=[i])
        thread.start()
        worker_threads.append(thread)

    clients: list[UserConnection] = []

    print(f'Port 번호 {FLAGS.port}에서 서버 동작 중')

    while not shutdown_requested:
        inputs = [server_sock]
        outputs = []

        with clients_after_processing_mutex:
            if clients_after_processing:
                clients.extend(clients_after_processing)
                clients_after_processing.clear()

        for client in clients:
            inputs.append(client.sock)
            outputs.append(client.sock)

        try:
            readable, writable, exceptionable = select.select(inputs, outputs, [], 0.1)

            if server_sock in readable:
                client_sock, addr = server_sock.accept()
                client = UserConnection(client_sock, addr)
                clients.append(client)
                print(f'새로운 클라이언트 접속 [{client}]')

            clients_to_remove: list[UserConnection] = []
            for client in clients:
                try:
                    if client.sock in writable and client.pending_data:
                        client.send_pending_data()

                    if client.sock in readable:
                        if client.receive_data():
                            with clients_for_processing_mutex:
                                clients_for_processing.append(client)
                                clients_for_processing_cv.notify()
                            clients_to_remove.append(client)

                except SocketClosed:
                    print(f'클라이언트 [{client}]: 상대방이 소켓을 닫았음')
                    client.disconnect()
                    clients_to_remove.append(client)

                except NoTypeFieldInMessage:
                    print(f'클라이언트 [{client}]: 메시지에 타입 필드가 없음')
                    client.disconnect()
                    clients_to_remove.append(client)

                except UnknownTypeInMessage as err:
                    print(f'클라이언트 [{client}]: 핸들러에 등록되지 않은 메시지 타입: {err}')
                    client.disconnect()
                    clients_to_remove.append(client)

                except socket.error as err:
                    if err.errno == errno.ECONNRESET:
                        print(f'클라이언트 [{client}]: 상대방이 소켓을 닫았음')
                    else:
                        print(f'소켓 에러: {err}')
                    client.disconnect()
                    clients_to_remove.append(client)

            for client in clients_to_remove:
                clients.remove(client)

        except KeyboardInterrupt:
            print('키보드로 프로그램 강제 종료 요청')
            on_cs_shutdown()

    print('Main thread 종료 중')

    for thread in worker_threads:
        print('작업 쓰레드 join() 시작')
        thread.join()
        print('작업 쓰레드 join() 완료')

    for client in clients:
        client.sock.close()

if __name__ == '__main__':
    app.run(main)
