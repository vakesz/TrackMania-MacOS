/* Host-only check of the early-settle decision in src/c/session_settle.h.
 * Each argument is a fact the session loop supplies; prints "early" or "wait". */
#include <stdio.h>
#include <stdlib.h>
#include "../src/c/session_settle.h"

int main(int argc, char **argv)
{
    SettleFacts facts = {0};
    if (argc != 11)
    {
        fputs("usage: root_known root_exited live_targets live_job ambiguous accounted "
                "job_query_ok active total known_job\n", stderr);
        return 2;
    }
    facts.root_known = atoi(argv[1]);
    facts.root_exited = atoi(argv[2]);
    facts.live_targets = strtoul(argv[3], NULL, 10);
    facts.live_job = strtoul(argv[4], NULL, 10);
    facts.ambiguous = strtoul(argv[5], NULL, 10);
    facts.accounted = atoi(argv[6]);
    facts.job_query_ok = atoi(argv[7]);
    facts.active_processes = strtoul(argv[8], NULL, 10);
    facts.total_processes = strtoul(argv[9], NULL, 10);
    facts.known_job = strtoul(argv[10], NULL, 10);
    puts(settle_may_end_early(&facts) ? "early" : "wait");
    return 0;
}
