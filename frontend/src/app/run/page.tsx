"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";
import { Loading } from "@/components/ui";

function Redirect() {
  const id = useSearchParams().get("id") ?? "";
  const router = useRouter();
  useEffect(() => {
    router.replace(id ? `/report?id=${encodeURIComponent(id)}` : "/");
  }, [id, router]);
  return <Loading what="Opening the report" />;
}

export default function RunRedirectPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Redirect />
    </Suspense>
  );
}
