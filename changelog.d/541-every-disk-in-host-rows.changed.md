- **The Host rows list every disk the server mounts.** They showed only the filesystem holding aegis, so a second disk with room to spare (`/mnt/data` beside a full `/`) was invisible. Each disk now has its own row in the Fleet band and the session sidebar, the `disk` tile shows the fullest, and a `host:` block hides mount points or adds ZFS and NFS ones.
help: config#disks
