#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>
#include <iostream>
#include <string.h>
#include <string>

using namespace std;

int main() {
    int port = 10212;
    int s = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);

    struct sockaddr_in sin;
    memset(&sin, 0, sizeof(sin));
    sin.sin_family = AF_INET;
    sin.sin_port = htons(port);
    sin.sin_addr.s_addr = INADDR_ANY;

    bind(s, (struct sockaddr*)&sin, sizeof(sin));
    cout << "서버 run 중 port : " << port <<  endl;

    while (true) {
        // 수신
        char buf[65536];
        memset(buf, 0, sizeof(buf));  // 버퍼를 초기화 안 하니까 앞서 보낸 메세지가 보낼 메세지보다 길면 오류남
        struct sockaddr_in client;
        socklen_t client_len = sizeof(client);
        int numBytes = recvfrom(s, buf, sizeof(buf), 0, (struct sockaddr*)&client, &client_len);

        cout << "받은 메시지: " << buf << endl;

        //  전송
        int sentBytes = sendto(s, buf, numBytes, 0, (struct sockaddr*)&client, client_len);

        cout << "전송" << endl;
    }

    close(s);
    return 0;
}