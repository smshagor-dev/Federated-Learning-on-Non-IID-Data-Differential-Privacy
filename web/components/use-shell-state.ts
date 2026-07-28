"use client";

import { useEffect, useState } from "react";

import {
  type CoordinatorAvailability,
  getCoordinatorHealth,
  getOverviewData,
  listAuditEventsWithToken,
  listProjectsWithToken,
  listResearchExperimentsWithToken,
} from "@/lib/api";
import { useStoredSession } from "@/lib/use-stored-session";
import type { AuditEvent, OverviewData, Project, ResearchExperimentSummary } from "@/types/api";

type ShellState = {
  loading: boolean;
  overview?: OverviewData;
  projects?: Project[];
  auditEvents?: AuditEvent[];
  researchExperiments?: ResearchExperimentSummary[];
  coordinatorAvailability?: CoordinatorAvailability;
};

export function useShellState() {
  const session = useStoredSession();
  const [state, setState] = useState<ShellState>({ loading: true });

  useEffect(() => {
    let active = true;

    async function load() {
      const overviewPromise = getOverviewData();

      if (!session?.token) {
        const overview = await overviewPromise;
        if (!active) {
          return;
        }
        setState({
          loading: false,
          overview,
          coordinatorAvailability: "unknown",
        });
        return;
      }

      const [overview, projects, auditEvents, researchExperiments, coordinator] = await Promise.all([
        overviewPromise,
        listProjectsWithToken(session.token).catch(() => undefined),
        listAuditEventsWithToken(session.token, 6).catch(() => undefined),
        listResearchExperimentsWithToken(session.token).catch(() => undefined),
        getCoordinatorHealth(session.token),
      ]);

      if (!active) {
        return;
      }

      setState({
        loading: false,
        overview,
        projects,
        auditEvents,
        researchExperiments,
        coordinatorAvailability: coordinator.availability,
      });
    }

    void load();

    return () => {
      active = false;
    };
  }, [session?.token]);

  return { session, ...state };
}
