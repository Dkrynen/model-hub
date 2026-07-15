import { Activity, FlaskConical } from "lucide-react";
import { ModelCompare } from "@/components/lab/model-compare";
import { PageHeader } from "@/components/page";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Performance } from "@/pages/performance";

export function Lab() {
  return (
    <>
      <PageHeader
        title="Lab"
        subtitle="Compare exact identity from the configured Ollama endpoint, then run one reproducible diagnostic protocol."
      />

      <Tabs defaultValue="compare">
        <TabsList aria-label="Lab views" className="w-full justify-start overflow-x-auto sm:w-auto">
          <TabsTrigger value="compare"><FlaskConical /> Compare</TabsTrigger>
          <TabsTrigger value="measure"><Activity /> Measure</TabsTrigger>
        </TabsList>
        <TabsContent value="compare">
          <ModelCompare />
        </TabsContent>
        <TabsContent value="measure">
          <Performance embedded />
        </TabsContent>
      </Tabs>
    </>
  );
}
