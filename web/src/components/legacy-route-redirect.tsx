import { Navigate, useLocation } from "react-router-dom";

export function LegacyRouteRedirect({
  to,
  preserveLocation = false,
}: {
  to: string;
  preserveLocation?: boolean;
}) {
  const location = useLocation();
  const suffix = preserveLocation ? `${location.search}${location.hash}` : "";
  return <Navigate to={`${to}${suffix}`} replace />;
}
