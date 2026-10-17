This folder holds `httpc`, the small HTTP client we use to talk to the inventory gateway. The gateway sheds load with 502, 503 and 504 during deploys and sometimes drops connections, and right now every one of those fails the caller. Add a retry policy to the client:

- at most 3 attempts per request, counting the first;
- wait 0.2 s before the second attempt and 0.4 s before the third;
- retry on a 502, 503 or 504 response and on a connection reset by the server;
- never retry any other status, 4xx included, and never retry a refused connection;
- when the attempts run out, raise what the last attempt raised, as the client does today.

Then make the whole test suite pass. Its integration tests run against a local fake server and take about four minutes.
