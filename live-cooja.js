TIMEOUT(95000);

var Socket = Java.type("java.net.Socket");
var Writer = Java.type("java.io.BufferedWriter");
var OutputStreamWriter = Java.type("java.io.OutputStreamWriter");
var System = Java.type("java.lang.System");
var socket = new Socket("127.0.0.1", BRIDGE_PORT);
socket.setTcpNoDelay(true);
var stream = new Writer(new OutputStreamWriter(socket.getOutputStream(), "UTF-8"));

function send(line) {
  stream.write(line + "\n");
  stream.flush();
}

function finish() {
  send("DONE");
  socket.close();
}

var paced = false;
var boundary = 80000;
// Schedule the first simulated cutoff from the first available mote event.
GENERATE_MSG(Math.ceil((boundary + 100) - time / 1000), "LIVE_CLOSE");
while (true) {
  if (!paced && time >= 75000000) {
    sim.setSpeedLimit(1.0);
    paced = true;
  }
  if (msg === "LIVE_CLOSE") {
    send("CLOSE," + boundary + "," + time);
    if (boundary === 94000) {
      finish();
      log.testOK();
    }
    boundary += 2000;
    GENERATE_MSG(Math.ceil((boundary + 100) - time / 1000), "LIVE_CLOSE");
  } else if (msg.indexOf("WSN,") >= 0) {
    var line = time + " ID:" + id + " " + msg;
    log.log(line + "\n");
    if (msg.indexOf("WSN,GEN,") >= 0 || msg.indexOf("WSN,RECV,") >= 0) {
      send("EVENT," + System.currentTimeMillis() + "," + line);
    }
  }
  YIELD();
}
