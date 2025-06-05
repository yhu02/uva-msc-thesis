# uva-msc-thesis

helm repo add litmuschaos https://litmuschaos.github.io/litmus-helm/
helm repo list

curl -sfL https://get.k3s.io | sh -
# Check for Ready node, takes ~30 seconds
sudo k3s kubectl get node


sudo cp /etc/rancher/k3s/k3s.yaml $HOME/.kube/config
sudo chown $USER:$USER $HOME/.kube/config

kubectl create ns litmus
# apply global permissions for litmus experiments
kubectl apply -f litmus-admin.yaml
kubectl create clusterrolebinding debug-argo-chaos \
  --clusterrole=cluster-admin \
  --serviceaccount=litmus:argo-chaos 

helm install chaos litmuschaos/litmus --namespace=litmus --set portal.frontend.service.type=NodePort

# wait
 kubectl get pods -A -w

# get hostname
hostname -I
# get port
  kubectl get svc -n litmus
# access ui
  http://172.30.126.200:30598/login

# create environment
# enable chaos
kubectl apply -f https://raw.githubusercontent.com/litmuschaos/litmus/master/mkdocs/docs/3.6.1/litmus-portal-crds-3.6.1.yml
kubectl apply -f infra-litmus-chaos-enable.yml

# create resilience probe


# create kubernetes dashboard

kubectl apply -f https://raw.githubusercontent.com/kubernetes/dashboard/v2.7.0/aio/deploy/recommended.yaml

kubectl proxy

# wait until dashboard is up
http://localhost:8001/api/v1/namespaces/kubernetes-dashboard/services/https:kubernetes-dashboard:/proxy/


# get secret and enter 
kubectl apply -f dashboard-admin.yaml

kubectl create token admin-user -n kubernetes-dashboard

# get experiment result

kubectl get chaosresult pod-cpu-hog-p3bcbsj6-pod-cpu-hog -n litmus -o json

# debugging

kubectl logs -n litmus -l app=subscriber
kubectl rollout restart deployment -n litmus
kubectl rollout restart statefulset -n litmus

AccessID mismatch generate new infra-litmus-chaos-enable.yml, 
reapply and delete subscriber pod
