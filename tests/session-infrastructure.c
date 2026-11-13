/* Host-only check of the rules in src/c/session_infrastructure.h.
 *   unrecognized watched baseline root in_job exited exit_code desktop_session
 *                console_verified desktop_verified service_verified
 *     prints "unrecognized" or "known"
 *   parent service in_job root watched baseline service_manager created_first
 *     prints "vouches" or "no" */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../src/c/session_infrastructure.h"

int main(int argc, char **argv)
{
    if (argc == 12 && !strcmp(argv[1], "unrecognized"))
    {
        ProcessFacts facts = {0};
        facts.watched = atoi(argv[2]);
        facts.baseline = atoi(argv[3]);
        facts.root = atoi(argv[4]);
        facts.in_job = atoi(argv[5]);
        facts.exited = atoi(argv[6]);
        facts.exit_code = strtoul(argv[7], NULL, 10);
        facts.desktop_session = atoi(argv[8]);
        facts.console_verified = atoi(argv[9]);
        facts.desktop_verified = atoi(argv[10]);
        facts.service_verified = atoi(argv[11]);
        puts(process_is_unrecognized(&facts) ? "unrecognized" : "known");
        return 0;
    }
    if (argc == 9 && !strcmp(argv[1], "parent"))
    {
        ParentFacts facts = {0};
        int service = atoi(argv[2]);
        facts.in_job = atoi(argv[3]);
        facts.root = atoi(argv[4]);
        facts.watched = atoi(argv[5]);
        facts.baseline = atoi(argv[6]);
        facts.service_manager = atoi(argv[7]);
        facts.created_first = atoi(argv[8]);
        puts(infrastructure_parent(service, &facts) ? "vouches" : "no");
        return 0;
    }
    fputs("usage: unrecognized FACTS... | parent FACTS...\n", stderr);
    return 2;
}
