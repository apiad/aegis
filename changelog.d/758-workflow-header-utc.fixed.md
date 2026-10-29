- **Workflow messages are stamped in UTC.** Their headers carried local time
  with no zone while every other header is UTC with `Z`, so on a host off UTC
  an agent read a workflow message hours before the notice that followed it.
