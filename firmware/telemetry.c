#include "telemetry.h"
#include "sys/energest.h"
#include <stdio.h>

/* Explicit big-endian wire layout, independent of platform alignment. */
static void
put(uint8_t *out, uint32_t value, unsigned count)
{
  while(count > 0) {
    out[--count] = (uint8_t)value;
    value >>= 8;
  }
}

static uint32_t
get(const uint8_t *in, unsigned count)
{
  uint32_t value = 0;
  while(count-- > 0) {
    value = (value << 8) | *in++;
  }
  return value;
}

void
reading_encode(uint8_t *bytes, const struct reading *r)
{
  bytes[0] = 1;
  put(bytes + 1, r->node, 2);
  put(bytes + 3, r->seq, 4);
  put(bytes + 7, r->local_seconds, 2);
  put(bytes + 9, r->temp_centi, 2);
  put(bytes + 11, r->humidity_centi, 2);
}

void
reading_decode(struct reading *r, const uint8_t *bytes)
{
  r->node = get(bytes + 1, 2);
  r->seq = get(bytes + 3, 4);
  r->local_seconds = get(bytes + 7, 2);
  r->temp_centi = get(bytes + 9, 2);
  r->humidity_centi = get(bytes + 11, 2);
}

PROCESS(energy_reporter, "Energest snapshots");
PROCESS_THREAD(energy_reporter, ev, data)
{
  static struct etimer timer;
  PROCESS_BEGIN();
  etimer_set(&timer, 15 * CLOCK_SECOND);
  while(1) {
    PROCESS_WAIT_EVENT_UNTIL(etimer_expired(&timer));
    energest_flush();
    printf("WSN,ENERGY,%lu,%llu,%llu,%llu,%llu,%llu\n",
           (unsigned long)ENERGEST_SECOND,
           (unsigned long long)energest_type_time(ENERGEST_TYPE_CPU),
           (unsigned long long)energest_type_time(ENERGEST_TYPE_LPM),
           (unsigned long long)energest_type_time(ENERGEST_TYPE_DEEP_LPM),
           (unsigned long long)energest_type_time(ENERGEST_TYPE_TRANSMIT),
           (unsigned long long)energest_type_time(ENERGEST_TYPE_LISTEN));
    etimer_reset(&timer);
  }
  PROCESS_END();
}
