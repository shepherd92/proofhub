#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>
#include <vector>

// Undirected Cayley graph: right multiplication by a, a^{-1}, b,
// where a=(1 2 ... n), b=(1 2). Permutations store zero-based images.
using Perm = std::array<unsigned, 10>;
int main(int argc, char** argv) {
    const unsigned n = argc > 1 ? std::stoul(argv[1]) : 10;
    if (n < 2 || n > 10) return 2;
    std::array<unsigned, 11> fac{};
    fac[0] = 1;
    for (unsigned i=1; i<=n; ++i) fac[i]=i*fac[i-1];
    auto rank = [&](const Perm& p) {
        unsigned r=0, used=0;
        for (unsigned i=0; i<n; ++i) {
            const unsigned smaller = p[i] - __builtin_popcount(used & ((1u<<p[i])-1));
            r += smaller * fac[n-1-i];
            used |= 1u<<p[i];
        }
        return r;
    };
    auto pack = [&](const Perm& p) {
        uint64_t x=0;
        for (unsigned i=0; i<n; ++i) x |= uint64_t(p[i])<<(4*i);
        return x;
    };
    auto unpack = [&](uint64_t x) {
        Perm p{};
        for (unsigned i=0; i<n; ++i) p[i]=(x>>(4*i))&15;
        return p;
    };
    auto step = [&](Perm p, unsigned g) {
        if (g==0) std::rotate(p.begin(),p.begin()+1,p.begin()+n);
        if (g==1) std::rotate(p.begin(),p.begin()+n-1,p.begin()+n);
        if (g==2) std::swap(p[0],p[1]);
        return p;
    };
    std::vector<int> dist(fac[n], -1);
    std::vector<uint64_t> queue;
    queue.reserve(fac[n]);
    Perm id{};
    for(unsigned i=0;i<n;++i) id[i]=i;
    queue.push_back(pack(id)); dist[rank(id)]=0;
    std::vector<unsigned> histogram(1,1);
    unsigned diameter=0;
    Perm farthest=id;
    for(size_t head=0; head<queue.size(); ++head) {
        const Perm p=unpack(queue[head]);
        const int d=dist[rank(p)];
        for(unsigned g=0; g<3; ++g) {
            const Perm q=step(p,g);
            const unsigned r=rank(q);
            if(dist[r]!=-1) continue;
            dist[r]=d+1;
            queue.push_back(pack(q));
            if(histogram.size()<=size_t(d+1)) histogram.resize(d+2,0);
            ++histogram[d+1];
            if(unsigned(d+1)>diameter) {diameter=d+1; farthest=q;}
        }
    }
    if(queue.size()!=fac[n]) return 3;
    // Independently check every edge against the computed distance labels.
    // Each nonidentity vertex must have a predecessor at distance d-1.
    for(uint64_t x:queue) {
        const Perm p=unpack(x);
        const int d=dist[rank(p)];
        bool predecessor=(d==0);
        for(unsigned g=0;g<3;++g) {
            const int e=dist[rank(step(p,g))];
            if(e<d-1 || e>d+1) return 4;
            if(e==d-1) predecessor=true;
        }
        if(!predecessor) return 5;
    }
    std::cout<<"n="<<n<<"\nvertices="<<queue.size()<<"\ndiameter="<<diameter<<"\nfarthest_images=";
    for(unsigned i=0;i<n;++i) std::cout<<(i?" ":"")<<farthest[i]+1;
    std::cout<<"\nshortest_word=";
    std::vector<unsigned> word;
    Perm p=farthest;
    while(dist[rank(p)]>0) {
        const int d=dist[rank(p)];
        for(unsigned g=0;g<3;++g) {
            Perm q=step(p,g);
            if(dist[rank(q)]==d-1) {
                word.push_back(g==2?2:1-g); p=q; break;
            }
        }
    }
    std::reverse(word.begin(),word.end());
    p=id;
    for(unsigned g:word) { std::cout<<(g==0?"a ":g==1?"A ":"b "); p=step(p,g); }
    if(p!=farthest || word.size()!=diameter) return 6;
    std::cout<<"\nchecks=passed\ndistance,count\n";
    for(unsigned d=0;d<histogram.size();++d) std::cout<<d<<","<<histogram[d]<<"\n";
}
