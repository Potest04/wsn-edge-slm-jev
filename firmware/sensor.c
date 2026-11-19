#include "telemetry.h"
#include "net/app-layer/coap/coap.h"
#include "net/ipv6/simple-udp.h"
#include "net/routing/routing.h"
#include "sys/node-id.h"
#include <stdio.h>

#if WSN_REPORT_STRIDE < 1
#error WSN_REPORT_STRIDE must be positive
#endif

static struct simple_udp_connection link;
static uint32_t sequence;

static void
sample(void)
{
  struct reading r;
  uip_ipaddr_t root;
  coap_message_t request[1];
  uint8_t payload[WSN_PAYLOAD_LEN], packet[WSN_COAP_MAX];
  unsigned length;
#if !WSN_LIVE_BURST
  const unsigned phase = (sequence + 7 * node_id) % 43;
#endif

  r.node = node_id;
  r.seq = sequence++;
  r.local_seconds = clock_seconds();
#if WSN_LIVE_BURST
  /* Sustained warm windows plus alerts from both a near and a relayed mote. */
  r.temp_centi = 2700 + (r.seq * 17 + r.node * 13) % 140;
  r.humidity_centi = 5500 + (r.seq * 31 + r.node * 73) % 500;
  if((r.node == 2 && r.seq > 0 && r.seq % 8 == 0) ||
     (r.node == 6 && r.seq == 36)) {
    r.temp_centi = 3300;
  }
#else
  r.temp_centi = 2110 + (r.node % 3) * 100 + (r.seq * 31 + r.node * 3) % 210;
  r.humidity_centi = 4100 + (r.seq * 61 + r.node * 311) % 1500;
  if(phase >= 27 && phase <= 30) {
    r.temp_centi += phase == 30 ? 1120 : 510 + (phase - 27) * 140;
    r.humidity_centi += 900;
  }
#endif
  printf("WSN,GEN,%u,%lu,%lu,%u,%u\n", r.node, (unsigned long)r.seq,
         (unsigned long)r.local_seconds, r.temp_centi, r.humidity_centi);

  /* The reporting experiment preserves a local hard-temperature override. */
  if(r.temp_centi < 3200 && r.seq % WSN_REPORT_STRIDE != 0) {
    return;
  }
  if(!NETSTACK_ROUTING.node_is_reachable() ||
     !NETSTACK_ROUTING.get_root_ipaddr(&root)) {
    return;
  }
  reading_encode(payload, &r);
  coap_init_message(request, COAP_TYPE_NON, COAP_POST, (uint16_t)r.seq);
  coap_set_header_uri_path(request, WSN_PATH);
  coap_set_header_content_format(request, APPLICATION_OCTET_STREAM);
  coap_set_payload(request, payload, sizeof(payload));
  length = coap_serialize_message(request, packet);
  if(length > 0) {
    simple_udp_sendto(&link, packet, length, &root);
    printf("WSN,SEND,%u,%lu,%u\n", r.node, (unsigned long)r.seq, length);
  }
}

PROCESS(sensor, "Environmental sensor");
AUTOSTART_PROCESSES(&sensor, &energy_reporter);

PROCESS_THREAD(sensor, ev, data)
{
  static struct etimer timer;
  PROCESS_BEGIN();
  simple_udp_register(&link, WSN_PORT, NULL, WSN_PORT, NULL);
#if WSN_LIVE_BURST
  etimer_set(&timer, 78 * CLOCK_SECOND);
#else
  etimer_set(&timer, 65 * CLOCK_SECOND + (node_id % 13) * CLOCK_SECOND / 3);
#endif
  while(1) {
    PROCESS_WAIT_EVENT_UNTIL(etimer_expired(&timer));
#if WSN_LIVE_BURST
    if(sequence == 48) {
      break;
    }
    sample();
    etimer_set(&timer, CLOCK_SECOND / 4);
#else
    if(clock_seconds() >= 220) {
      break;
    }
    sample();
    etimer_set(&timer, (sequence % 41 < 8 ? 1 : 4) * CLOCK_SECOND +
               (node_id % 5) * CLOCK_SECOND / 16);
#endif
  }
  PROCESS_END();
}
