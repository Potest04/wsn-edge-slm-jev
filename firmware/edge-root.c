#include "telemetry.h"
#include "net/app-layer/coap/coap.h"
#include "net/ipv6/simple-udp.h"
#include "net/routing/routing.h"
#include <stdio.h>
#include <string.h>

static struct simple_udp_connection link;

static void
receive(struct simple_udp_connection *connection, const uip_ipaddr_t *sender,
        uint16_t sender_port, const uip_ipaddr_t *receiver,
        uint16_t receiver_port, const uint8_t *bytes, uint16_t length)
{
  coap_message_t request[1];
  uint8_t packet[WSN_COAP_MAX + 1];
  const char *uri;
  const uint8_t *payload;
  unsigned content_format;
  struct reading r;

  if(length > WSN_COAP_MAX) {
    return;
  }
  memcpy(packet, bytes, length);
  packet[length] = 0;
  if(coap_parse_message(request, packet, length) != NO_ERROR ||
     request->code != COAP_POST ||
     coap_get_header_uri_path(request, &uri) != sizeof(WSN_PATH) - 1 ||
     memcmp(uri, WSN_PATH, sizeof(WSN_PATH) - 1) != 0 ||
     !coap_get_header_content_format(request, &content_format) ||
     content_format != APPLICATION_OCTET_STREAM ||
     coap_get_payload(request, &payload) != WSN_PAYLOAD_LEN || payload[0] != 1) {
    return;
  }
  reading_decode(&r, payload);
  printf("WSN,RECV,%u,%lu,%lu,%u,%u\n", r.node, (unsigned long)r.seq,
         (unsigned long)r.local_seconds, r.temp_centi, r.humidity_centi);
}

PROCESS(edge_root, "RPL root and CoAP telemetry collector");
AUTOSTART_PROCESSES(&edge_root, &energy_reporter);

PROCESS_THREAD(edge_root, ev, data)
{
  PROCESS_BEGIN();
  NETSTACK_ROUTING.root_start();
  simple_udp_register(&link, WSN_PORT, NULL, WSN_PORT, receive);
  while(1) {
    PROCESS_YIELD();
  }
  PROCESS_END();
}
