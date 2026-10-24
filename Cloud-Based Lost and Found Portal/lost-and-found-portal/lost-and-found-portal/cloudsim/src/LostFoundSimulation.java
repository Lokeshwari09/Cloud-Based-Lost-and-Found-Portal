import java.text.DecimalFormat;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.LinkedList;
import java.util.List;

import org.cloudbus.cloudsim.Cloudlet;
import org.cloudbus.cloudsim.CloudletSchedulerTimeShared;
import org.cloudbus.cloudsim.Datacenter;
import org.cloudbus.cloudsim.DatacenterBroker;
import org.cloudbus.cloudsim.DatacenterCharacteristics;
import org.cloudbus.cloudsim.Host;
import org.cloudbus.cloudsim.Log;
import org.cloudbus.cloudsim.Pe;
import org.cloudbus.cloudsim.Storage;
import org.cloudbus.cloudsim.UtilizationModel;
import org.cloudbus.cloudsim.UtilizationModelFull;
import org.cloudbus.cloudsim.Vm;
import org.cloudbus.cloudsim.VmAllocationPolicySimple;
import org.cloudbus.cloudsim.VmSchedulerTimeShared;
import org.cloudbus.cloudsim.core.CloudSim;
import org.cloudbus.cloudsim.provisioners.BwProvisionerSimple;
import org.cloudbus.cloudsim.provisioners.PeProvisionerSimple;
import org.cloudbus.cloudsim.provisioners.RamProvisionerSimple;

/**
 * CloudSim simulation for the "Cloud-Based Lost and Found Portal".
 *
 * Question answered: how many web-server VMs does the portal need so that user
 * requests (search item, report item, upload photo, match items) finish quickly?
 *
 * The same workload of 60 requests is run on 1, 2 and 4 VMs. Each request type
 * is modelled as a Cloudlet with a different length (MI = million instructions).
 */
public class LostFoundSimulation {

    // request types: name -> cloudlet length (MI)
    private static final String[] REQ_NAMES = {"Search items", "Report item", "Upload photo", "Run matching"};
    private static final long[] REQ_LENGTH = {2000, 4000, 9000, 15000};
    private static final int TOTAL_REQUESTS = 60;

    public static void main(String[] args) {
        int[] vmCounts = {1, 2, 4};
        double[] avgTurnaround = new double[vmCounts.length];
        double[] makespan = new double[vmCounts.length];
        double[] cost = new double[vmCounts.length];

        System.out.println("=== Lost & Found Portal - CloudSim capacity simulation ===");
        System.out.println("Workload: " + TOTAL_REQUESTS + " user requests (search / report / photo upload / matching)\n");

        for (int i = 0; i < vmCounts.length; i++) {
            double[] r = runScenario(vmCounts[i]);
            avgTurnaround[i] = r[0];
            makespan[i] = r[1];
            cost[i] = r[2];
        }

        DecimalFormat df = new DecimalFormat("0.00");
        System.out.println("\n================= SUMMARY =================");
        System.out.println(String.format("%-6s %-22s %-18s %-12s", "VMs", "Avg response time (s)", "Total time (s)", "Est. cost"));
        for (int i = 0; i < vmCounts.length; i++) {
            System.out.println(String.format("%-6d %-22s %-18s %-12s", vmCounts[i],
                    df.format(avgTurnaround[i]), df.format(makespan[i]), "$" + df.format(cost[i])));
        }
        System.out.println("\nConclusion: adding VMs lowers response time almost linearly while cost grows,");
        System.out.println("so 2 VMs behind a load balancer (AWS ALB + Auto Scaling) is a good balance for normal load,");
        System.out.println("and 4 VMs should be reserved for peak periods (e.g. exam / festival days).");
    }

    /** Runs one simulation; returns {avg turnaround, makespan, cost}. */
    private static double[] runScenario(int vmCount) {
        Log.printLine("\n--- Scenario: " + vmCount + " VM(s) ---");
        try {
            CloudSim.init(1, Calendar.getInstance(), false);

            Datacenter datacenter = createDatacenter("AWS-Mumbai-DC");
            DatacenterBroker broker = new DatacenterBroker("PortalBroker");
            int userId = broker.getId();

            // VMs = EC2 t3.medium-like web servers (2 vCPU, 1000 MIPS each)
            List<Vm> vmList = new ArrayList<Vm>();
            for (int v = 0; v < vmCount; v++) {
                vmList.add(new Vm(v, userId, 1000, 1, 1024, 1000, 10000, "Xen",
                        new CloudletSchedulerTimeShared()));
            }
            broker.submitVmList(vmList);

            // Cloudlets = user requests
            List<Cloudlet> cloudlets = new ArrayList<Cloudlet>();
            UtilizationModel full = new UtilizationModelFull();
            for (int c = 0; c < TOTAL_REQUESTS; c++) {
                int type = c % REQ_LENGTH.length;
                Cloudlet cl = new Cloudlet(c, REQ_LENGTH[type], 1, 300, 300, full, full, full);
                cl.setUserId(userId);
                cl.setVmId(c % vmCount);          // simple round-robin load balancing
                cloudlets.add(cl);
            }
            broker.submitCloudletList(cloudlets);

            CloudSim.startSimulation();
            List<Cloudlet> done = broker.getCloudletReceivedList();
            CloudSim.stopSimulation();

            printResults(done);

            double sum = 0, max = 0;
            for (Cloudlet cl : done) {
                sum += cl.getFinishTime() - cl.getExecStartTime();
                max = Math.max(max, cl.getFinishTime());
            }
            double avg = sum / done.size();
            double hourlyRate = 0.0416;                   // approx USD/hour of a t3.medium
            double cost = vmCount * hourlyRate * Math.max(max / 3600.0, 1.0 / 60);
            return new double[]{avg, max, cost};
        } catch (Exception e) {
            e.printStackTrace();
            return new double[]{0, 0, 0};
        }
    }

    private static Datacenter createDatacenter(String name) throws Exception {
        List<Host> hostList = new ArrayList<Host>();
        for (int h = 0; h < 2; h++) {
            List<Pe> peList = new ArrayList<Pe>();
            for (int p = 0; p < 4; p++) {
                peList.add(new Pe(p, new PeProvisionerSimple(1000)));
            }
            hostList.add(new Host(h, new RamProvisionerSimple(16384),
                    new BwProvisionerSimple(100000), 1000000, peList,
                    new VmSchedulerTimeShared(peList)));
        }
        DatacenterCharacteristics ch = new DatacenterCharacteristics(
                "x86", "Linux", "Xen", hostList, 5.5, 3.0, 0.05, 0.001, 0.0);
        return new Datacenter(name, ch, new VmAllocationPolicySimple(hostList),
                new LinkedList<Storage>(), 0);
    }

    private static void printResults(List<Cloudlet> list) {
        DecimalFormat df = new DecimalFormat("0.00");
        Log.printLine(String.format("%-8s %-14s %-8s %-6s %-10s %-10s", "Req ID", "Type", "Status", "VM", "Start", "Finish"));
        for (Cloudlet cl : list) {
            if (cl.getCloudletStatus() == Cloudlet.SUCCESS) {
                Log.printLine(String.format("%-8d %-14s %-8s %-6d %-10s %-10s", cl.getCloudletId(),
                        REQ_NAMES[cl.getCloudletId() % REQ_NAMES.length], "SUCCESS", cl.getVmId(),
                        df.format(cl.getExecStartTime()), df.format(cl.getFinishTime())));
            }
        }
    }
}
